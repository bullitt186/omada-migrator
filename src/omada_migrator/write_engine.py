"""Write engine with ID remapping and retry-until-stable (FR-7, FR-8, FR-9)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

# Reference field → resource type mapping (TD-4)
# Scalar fields: value is a single ID string
REFERENCE_FIELDS: dict[str, str] = {
    "networkId": "lan-networks",
    "nativeNetworkId": "lan-networks",
    "vlanId": "lan-vlans",
    "radiusProfileId": "profiles/radius",
    "gatewayId": "gateways",
    "profileId": "lan-profiles",
    "wlanId": "wireless-network/wlans",
    "ipGroupId": "ip-groups",
    "urlFilterId": "url-filters",
    "netId": "lan-networks",
    "scheduleId": "time-range-profiles",
    "macFilterId": "mac-filters",
    "macGroupId": "mac-filters",
    "portalId": "portals",
    "voiceNetworkId": "lan-networks",
}
# List fields: value is a list of ID strings
REFERENCE_LIST_FIELDS: dict[str, str] = {
    "tagNetworkIds": "lan-networks",
    "untagNetworkIds": "lan-networks",
    "lanNetworkIds": "lan-networks",
    "networkIds": "lan-networks",
    "wanPortIds": "internet/ports-config",
    "ouiProfileIdList": "oui-profiles",
}


class OpType(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class WriteOp:
    op_type: OpType
    resource_key: str
    object_name: str
    payload: dict[str, Any]
    url: str
    source_id: str | None = None


class WritePlan:
    def __init__(self):
        self.ops: list[WriteOp] = []

    def add(self, op: WriteOp) -> None:
        self.ops.append(op)


class IdMapper:
    """Maintains source-ID → target-ID mapping (FR-7)."""

    def __init__(self):
        self._map: dict[str, dict[str, str]] = {}

    def add(self, resource_type: str, source_id: str, target_id: str) -> None:
        self._map.setdefault(resource_type, {})[source_id] = target_id

    def resolve(self, resource_type: str, source_id: str) -> str | None:
        return self._map.get(resource_type, {}).get(source_id)


class UnresolvedRefError(Exception):
    """Raised when a reference field cannot be remapped."""

    def __init__(self, field_name: str):
        super().__init__(f"Unresolved reference: {field_name}")
        self.field_name = field_name


def split_payload_for_writes(payload: dict[str, Any], split_writes: dict[str, list[str]]) -> list[tuple[str, dict[str, Any]]]:
    """Split a full object payload into per-endpoint sub-payloads.

    Returns list of (endpoint_path, sub_payload) for each endpoint that has
    matching fields in the payload. Fields not mapped to any endpoint go to
    the first endpoint (primary write).
    """
    if not split_writes:
        return []

    results: list[tuple[str, dict[str, Any]]] = []
    claimed_fields: set[str] = set()
    endpoints = list(split_writes.items())

    for endpoint, fields in endpoints:
        sub = {k: v for k, v in payload.items() if k in fields}
        if sub:
            results.append((endpoint, sub))
            claimed_fields.update(sub.keys())

    return results


def remap_references(payload: dict[str, Any], mapper: IdMapper) -> tuple[dict[str, Any], list[str]]:
    """Rewrite reference fields from source IDs to target IDs.

    Recurses into nested dicts to catch fields like vlanSetting.networkId.
    Returns (remapped_payload, list_of_unresolved_field_names).
    """
    result = dict(payload)
    unresolved: list[str] = []
    _remap_dict(result, mapper, unresolved, "")
    return result, unresolved


def _remap_dict(d: dict[str, Any], mapper: IdMapper, unresolved: list[str], prefix: str) -> None:
    """In-place remap of reference fields, recursing into nested dicts."""
    for field_name, resource_type in REFERENCE_FIELDS.items():
        if field_name not in d:
            continue
        source_id = d[field_name]
        if not isinstance(source_id, str):
            continue
        target_id = mapper.resolve(resource_type, source_id)
        if target_id is not None:
            d[field_name] = target_id
        else:
            unresolved.append(prefix + field_name)

    for field_name, resource_type in REFERENCE_LIST_FIELDS.items():
        if field_name not in d:
            continue
        source_ids = d[field_name]
        if not isinstance(source_ids, list):
            continue
        remapped_ids = []
        for sid in source_ids:
            if not isinstance(sid, str):
                remapped_ids.append(sid)
                continue
            target_id = mapper.resolve(resource_type, sid)
            if target_id is not None:
                remapped_ids.append(target_id)
            else:
                unresolved.append(prefix + field_name)
                break
        else:
            d[field_name] = remapped_ids

    # Recurse into nested dicts
    for key, val in d.items():
        if isinstance(val, dict) and not key.startswith("_"):
            _remap_dict(val, mapper, unresolved, prefix + key + ".")


async def execute_plan(
    plan: WritePlan,
    executor: Callable[[WriteOp], Awaitable[dict]],
    mapper: IdMapper,
) -> dict[str, Any]:
    """Execute write plan with retry-until-stable (FR-9).

    Passes repeat until queue is empty or no forward progress.
    """
    queue = list(plan.ops)
    succeeded = 0
    failures: list[dict[str, Any]] = []

    while queue:
        next_queue: list[WriteOp] = []
        progress = False

        for op in queue:
            try:
                result = await executor(op)
                succeeded += 1
                progress = True
                # Record mapping for created objects
                if op.op_type == OpType.CREATE and op.source_id and "id" in result:
                    mapper.add(op.resource_key, op.source_id, result["id"])
            except UnresolvedRefError:
                next_queue.append(op)
            except Exception as e:
                failures.append({"op": op, "reason": str(e)})
                progress = True  # Don't retry real errors

        if not progress:
            for op in next_queue:
                failures.append({"op": op, "reason": f"Unresolved reference (networkId): stuck after convergence"})
            break

        queue = next_queue

    return {
        "succeeded": succeeded,
        "failed": len(failures),
        "failures": failures,
    }
