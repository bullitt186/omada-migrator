"""Diff engine for comparing source/target objects (FR-5)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

IGNORED_FIELDS = frozenset({
    # Server-assigned identifiers and timestamps
    "id", "ssidId", "wlanId", "createTime", "modifyTime", "updateTime", "createUser",
    "modifyUser", "siteId", "omadacId",
    # Internal tracking fields
    "_parents", "_parent",
    # Controller-specific metadata (don't match across controllers)
    "tagIds", "tagName", "site", "deviceMac", "interfaceIds",
    # Computed/read-only state
    "totalIpNum", "dhcpServerNum", "gatewayModel", "portList",
    "boundDeviceNum", "existCustomDhcpOption", "existAllowInternetAccess",
    "existLargePingThreshold", "existIcmpTimestampRequestReject",
    "supportIcmpTimestampRequestReject",
    # Per-object IDs in read responses (not references to other objects)
    "groupId",
    # Per-device hardware-specific fields
    "ip", "ledSetting",
})

NATURAL_KEY_CANDIDATES = ["name", "ssid", "profileName", "ssidName", "ruleName", "policyName"]


class DiffStatus(Enum):
    IDENTICAL = "identical"
    DIFFERS = "differs"


@dataclass
class DiffResult:
    status: DiffStatus
    changed_fields: list[str] = field(default_factory=list)


def match_objects(
    source: list[dict], target: list[dict], key_fields: list[str] | None = None
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match source and target objects by natural key (FR-5)."""
    if key_fields is None:
        key_fields = NATURAL_KEY_CANDIDATES

    # Singleton shortcut: if both sides have exactly 1 object, match them directly
    if len(source) == 1 and len(target) == 1:
        return [(source[0], target[0])], [], []

    # Find which key field is present in the data
    key_field = None
    if source:
        for kf in key_fields:
            if kf in source[0]:
                key_field = kf
                break

    matched = []
    source_only = []
    target_remaining = list(target)

    for src_obj in source:
        match_found = False
        src_key = src_obj.get(key_field) if key_field else None

        if src_key is not None:
            for i, tgt_obj in enumerate(target_remaining):
                if tgt_obj.get(key_field) == src_key:
                    matched.append((src_obj, tgt_obj))
                    target_remaining.pop(i)
                    match_found = True
                    break
        else:
            # Fallback: match by id
            src_id = src_obj.get("id")
            if src_id:
                for i, tgt_obj in enumerate(target_remaining):
                    if tgt_obj.get("id") == src_id:
                        matched.append((src_obj, tgt_obj))
                        target_remaining.pop(i)
                        match_found = True
                        break

        if not match_found:
            source_only.append(src_obj)

    return matched, source_only, target_remaining


def diff_objects(source: dict, target: dict, ignore_fields: frozenset[str] | None = None) -> DiffResult:
    """Compare two matched objects, ignoring server-assigned fields."""
    if ignore_fields is None:
        ignore_fields = IGNORED_FIELDS

    # Guard: if either side isn't a dict, treat as different
    if not isinstance(source, dict) or not isinstance(target, dict):
        return DiffResult(status=DiffStatus.DIFFERS, changed_fields=["_type_mismatch"])

    all_keys = set(source.keys()) | set(target.keys())
    changed = []

    for key in all_keys:
        if key in ignore_fields:
            continue
        src_val = source.get(key)
        tgt_val = target.get(key)
        if src_val != tgt_val:
            changed.append(key)

    if changed:
        return DiffResult(status=DiffStatus.DIFFERS, changed_fields=changed)
    return DiffResult(status=DiffStatus.IDENTICAL)


def diff_resource(
    source: list[dict], target: list[dict], key_fields: list[str] | None = None
) -> dict[str, Any]:
    """Diff an entire resource type, returning summary counts and details."""
    matched, source_only, target_only = match_objects(source, target, key_fields)

    identical = 0
    differs = 0
    diff_details = []

    for src_obj, tgt_obj in matched:
        result = diff_objects(src_obj, tgt_obj)
        if result.status == DiffStatus.IDENTICAL:
            identical += 1
        else:
            differs += 1
            diff_details.append({"source": src_obj, "target": tgt_obj, "changed_fields": result.changed_fields})

    return {
        "identical": identical,
        "differs": differs,
        "source_only": len(source_only),
        "target_only": len(target_only),
        "diff_details": diff_details,
        "source_only_items": source_only,
        "target_only_items": target_only,
    }
