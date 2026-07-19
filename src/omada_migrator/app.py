"""FastAPI application — serves API + static UI (FR-12)."""

import json as json_mod
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .api_client import OmadaClient, OmadaApiError
from .auth import (
    AuthStrategy,
    ClientCredentialsAuth,
    WebSessionAuth,
    discover_omadac_id,
)
from .config import ConfigStore
from .diff import diff_resource
from .quirks import apply_write_quirks, READ_EXTRA_PARAMS
from .registry import ResourceRegistry
from .schema_meta import SchemaMeta
from .snapshot import save_snapshot, load_snapshot
from .spec_store import list_specs, get_spec_path, import_spec, delete_spec
from .write_engine import (
    WriteOp,
    OpType,
    IdMapper,
    remap_references,
    UnresolvedRefError,
    split_payload_for_writes,
)

app = FastAPI(title="Omada Migrator")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = Path.home() / ".config" / "omada-migrator" / "config.json"
SNAPSHOT_DIR = Path.home() / ".config" / "omada-migrator" / "snapshots"

config_store = ConfigStore(CONFIG_PATH)
config_store.load()

_registry: ResourceRegistry | None = None
_schema_meta: SchemaMeta | None = None
_current_spec_path: Path | None = None
_connections: dict[str, tuple[httpx.AsyncClient, OmadaClient]] = {}


def _spec_path() -> Path:
    """Resolve active spec path (latest user-provided, or bundled fallback)."""
    return get_spec_path()


def get_registry() -> ResourceRegistry:
    global _registry, _current_spec_path
    spec = _spec_path()
    if _registry is None or _current_spec_path != spec:
        _registry = ResourceRegistry.from_spec(spec)
        _current_spec_path = spec
    return _registry


def get_schema_meta() -> SchemaMeta:
    global _schema_meta, _current_spec_path
    spec = _spec_path()
    if _schema_meta is None or _current_spec_path != spec:
        _schema_meta = SchemaMeta.from_spec(spec)
    return _schema_meta


def reload_spec():
    """Force reload registry and schema meta (after spec import/delete)."""
    global _registry, _schema_meta, _current_spec_path
    _registry = None
    _schema_meta = None
    _current_spec_path = None


async def get_connection(profile_name: str) -> OmadaClient:
    """Get or create a connection for a profile."""
    if profile_name in _connections:
        return _connections[profile_name][1]

    profile = config_store.get_profile(profile_name)
    if not profile:
        raise HTTPException(404, f"Profile '{profile_name}' not found")

    verify_ssl = not profile.get("insecure_tls", False)
    http_client = httpx.AsyncClient(verify=verify_ssl, timeout=30.0)

    ptype = profile["type"]
    if ptype == "fusion":
        omadac_id = await discover_omadac_id(http_client, profile["url"])
        auth: AuthStrategy = WebSessionAuth(
            base_url=profile["url"],
            omadac_id=omadac_id,
            username=profile["username"],
            password=profile["password"],
        )
    else:
        omadac_id = profile["omadac_id"]
        auth = ClientCredentialsAuth(
            base_url=profile["url"],
            omadac_id=omadac_id,
            client_id=profile["client_id"],
            client_secret=profile["client_secret"],
        )

    auth.set_client(http_client)
    await auth.authenticate()

    client = OmadaClient(
        http_client=http_client, auth=auth,
        base_url=profile["url"], omadac_id=omadac_id,
    )
    _connections[profile_name] = (http_client, client)
    return client


# --- Profile API (FR-1) ---

class ProfileCreate(BaseModel):
    name: str
    type: str
    url: str
    insecure_tls: bool = False
    omadac_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    username: str | None = None
    password: str | None = None


@app.get("/api/profiles")
def list_profiles():
    config_store.load()
    # Return profiles without secrets
    return [
        {k: v for k, v in p.items() if k not in ("client_secret", "password")}
        for p in config_store.profiles
    ]


@app.post("/api/profiles")
def create_profile(profile: ProfileCreate):
    config_store.add_profile(profile.model_dump(exclude_none=True))
    config_store.save()
    return {"status": "ok"}


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    config_store.remove_profile(name)
    config_store.save()
    return {"status": "ok"}


# --- Site API (FR-2) ---

@app.get("/api/profiles/{name}/sites")
async def list_sites(name: str):
    client = await get_connection(name)
    return await client.get_sites()


# --- Controller info (version, API version) ---

@app.get("/api/profiles/{name}/controller-info")
async def get_controller_info(name: str):
    """Get controller version, firmware, model, and supported API versions."""
    client = await get_connection(name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/system/setting/controller-status"
    info: dict[str, Any] = {}
    try:
        data = await client.request("GET", url)
        result = data.get("result", {})
        info = {
            "controllerVersion": result.get("controllerVersion", ""),
            "firmwareVersion": result.get("firmwareVersion", ""),
            "model": result.get("model", ""),
            "name": result.get("name", ""),
            "category": result.get("category", ""),
            "controllerType": result.get("controllerType"),
        }
    except (OmadaApiError, Exception):
        pass

    # Detect supported API versions by probing
    api_versions = ["v1"]
    try:
        probe_url = f"{client._base_url}/openapi/v2/{client._omadac_id}/sites"
        await client.request("GET", probe_url, params={"pageSize": 1, "page": 1})
        api_versions.append("v2")
    except (OmadaApiError, Exception):
        pass

    info["apiVersions"] = api_versions
    return info


# --- Schema metadata for UI ---

@app.get("/api/schema-meta")
def get_schema_metadata():
    """Return human-readable descriptions for resource keys and fields."""
    meta = get_schema_meta()
    return meta.to_dict()


# --- Spec management ---

@app.get("/api/specs")
def list_available_specs():
    """List available OpenAPI specs (bundled + user-imported)."""
    specs = list_specs()
    active = str(_spec_path())
    for s in specs:
        s["active"] = s["path"] == active
    return specs


class SpecImportRequest(BaseModel):
    source_path: str
    label: str | None = None


@app.post("/api/specs/import")
def import_spec_file(req: SpecImportRequest):
    """Import an OpenAPI spec from a local file path."""
    try:
        result = import_spec(req.source_path, req.label)
        reload_spec()
        return {"status": "ok", **result}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.delete("/api/specs/{name}")
def delete_spec_file(name: str):
    """Delete a user-imported spec."""
    if delete_spec(name):
        reload_spec()
        return {"status": "ok"}
    raise HTTPException(404, "Spec not found or is bundled (cannot delete)")


# --- Read / Snapshot (FR-3, FR-4) ---

# Parent param → (list endpoint suffix, field to extract)
PARENT_RESOLVERS: dict[str, tuple[str, str]] = {
    "apMac": ("/openapi/v1/{omadacId}/sites/{siteId}/devices", "mac"),
    "switchMac": ("/openapi/v1/{omadacId}/sites/{siteId}/devices", "mac"),
    "gatewayMac": ("/openapi/v1/{omadacId}/sites/{siteId}/devices", "mac"),
    "deviceMac": ("/openapi/v1/{omadacId}/sites/{siteId}/devices", "mac"),
    "stackId": ("/openapi/v1/{omadacId}/sites/{siteId}/stacks", "stackId"),
    "wlanId": ("/openapi/v1/{omadacId}/sites/{siteId}/wireless-network/wlans", "wlanId"),
    "portalId": ("/openapi/v1/{omadacId}/sites/{siteId}/portals", "id"),
    "profileId": ("/openapi/v1/{omadacId}/sites/{siteId}/ppsk-profiles", "id"),
}

# Device type filtering for MAC-based params
_DEVICE_TYPE_FOR_PARAM: dict[str, str] = {
    "apMac": "ap",
    "switchMac": "switch",
    "gatewayMac": "gateway",
}


async def resolve_parent_values(client: OmadaClient, site_id: str, param: str) -> list[str]:
    """Resolve parent param values by listing the parent resource."""
    resolver = PARENT_RESOLVERS.get(param)
    if not resolver:
        return []

    endpoint, field = resolver
    url = endpoint.replace("{omadacId}", client._omadac_id).replace("{siteId}", site_id)
    full_url = f"{client._base_url}{url}"

    try:
        items = await client.get_paginated(full_url)
    except OmadaApiError:
        return []

    # Filter by device type if applicable
    device_type = _DEVICE_TYPE_FOR_PARAM.get(param)
    if device_type and field == "mac":
        items = [i for i in items if i.get("type", "").lower() == device_type]

    return [item[field] for item in items if field in item]


def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json_mod.dumps(data)}\n\n"


@app.get("/api/read")
async def read_site_sse(profile_name: str, site_id: str, site_name: str = ""):
    """Read all resources via SSE for progress streaming."""

    async def generate() -> AsyncGenerator[str, None]:
        client = await get_connection(profile_name)
        registry = get_registry()
        entries = registry.entries()
        total = len(entries)

        resources: dict[str, Any] = {}
        for i, entry in enumerate(entries):
            try:
                url = entry.list_path
                if not url:
                    continue

                if entry.parent_params:
                    # Only attempt if we can resolve all parent params
                    unsupported_params = [p for p in entry.parent_params if p not in PARENT_RESOLVERS]
                    if unsupported_params:
                        resources[entry.key] = {
                            "status": "unsupported",
                            "error": f"Cannot resolve parent: {', '.join(unsupported_params)}",
                            "objects": [],
                        }
                        yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": "unsupported"})
                        continue

                    # Resolve all parent params, then iterate all combinations
                    from itertools import product as iterproduct
                    param_values: list[list[tuple[str, str]]] = []
                    for param in entry.parent_params:
                        values = await resolve_parent_values(client, site_id, param)
                        param_values.append([(param, v) for v in values])

                    # If any param resolves to empty, skip
                    if not all(param_values):
                        resources[entry.key] = {"status": "ok", "objects": []}
                        yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": "ok", "count": 0})
                        continue

                    objects = []
                    for combo in iterproduct(*param_values):
                        purl = url.replace("{omadacId}", client._omadac_id).replace("{siteId}", site_id)
                        parent_info = {}
                        for param_name, param_val in combo:
                            purl = purl.replace("{" + param_name + "}", param_val)
                            parent_info[param_name] = param_val
                        full_url = f"{client._base_url}{purl}"
                        try:
                            nested_extra = READ_EXTRA_PARAMS.get(entry.key)
                            if entry.is_list:
                                items = await client.get_paginated(full_url, extra_params=nested_extra)
                            else:
                                item = await client.get_singleton(full_url, extra_params=nested_extra)
                                items = [item] if item else []
                            for obj in items:
                                obj["_parents"] = parent_info
                            objects.extend(items)
                        except OmadaApiError:
                            pass

                    resources[entry.key] = {"status": "ok", "objects": objects}
                    yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": "ok", "count": len(objects)})
                    continue

                url = url.replace("{omadacId}", client._omadac_id)
                url = url.replace("{siteId}", site_id)
                full_url = f"{client._base_url}{url}"

                extra_params = READ_EXTRA_PARAMS.get(entry.key)
                if entry.is_list:
                    objects = await client.get_paginated(full_url, extra_params=extra_params)
                else:
                    obj = await client.get_singleton(full_url, extra_params=extra_params)
                    objects = [obj] if obj else []

                resources[entry.key] = {"status": "ok", "objects": objects}
                yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": "ok", "count": len(objects)})
            except OmadaApiError as e:
                status = "unsupported" if e.is_unsupported else "error"
                resources[entry.key] = {"status": status, "error": str(e), "objects": []}
                yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": status})
            except Exception as e:
                resources[entry.key] = {"status": "error", "error": str(e), "objects": []}
                yield _sse_event("progress", {"current": i + 1, "total": total, "key": entry.key, "status": "error"})

        profile = config_store.get_profile(profile_name)
        snapshot_data = {
            "controller": {"name": profile["name"], "type": profile["type"], "url": profile["url"]},
            "site": {"site_id": site_id, "name": site_name or site_id},
            "resources": resources,
        }
        path = save_snapshot(snapshot_data, SNAPSHOT_DIR)
        yield _sse_event("done", {"snapshot_path": str(path), "resource_count": len(resources)})

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Snapshot management ---

@app.get("/api/snapshots")
def list_snapshots():
    if not SNAPSHOT_DIR.exists():
        return []
    return [
        {"name": f.name, "path": str(f)}
        for f in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True)
    ]


@app.get("/api/snapshots/{filename}")
def get_snapshot(filename: str):
    path = SNAPSHOT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Snapshot not found")
    return load_snapshot(path)


@app.delete("/api/snapshots/{filename}")
def delete_snapshot(filename: str):
    path = SNAPSHOT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Snapshot not found")
    path.unlink()
    return {"status": "ok"}


# --- Diff (FR-5) ---

class DiffRequest(BaseModel):
    source_snapshot: str
    target_profile: str
    target_site_id: str


@app.post("/api/diff")
async def compute_diff(req: DiffRequest):
    """Compute diff between a source snapshot and live target, streamed via SSE for progress."""

    async def generate() -> AsyncGenerator[str, None]:
        try:
            source = load_snapshot(req.source_snapshot)
        except (FileNotFoundError, Exception) as e:
            yield _sse_event("diff_done", {"_error": str(e)})
            return
        try:
            target_client = await get_connection(req.target_profile)
        except (HTTPException, Exception) as e:
            yield _sse_event("diff_done", {"_error": str(e)})
            return
        registry = get_registry()

        resource_keys = [k for k in source["resources"] if source["resources"][k]["status"] == "ok"]
        total = len(resource_keys)
        results: dict[str, Any] = {}

        # Include non-ok resources directly
        for key, src_data in source["resources"].items():
            if src_data["status"] != "ok":
                results[key] = {"status": src_data["status"], "error": src_data.get("error")}

        for i, key in enumerate(resource_keys):
            src_data = source["resources"][key]
            entry = registry.get(key)
            if not entry or not entry.list_path:
                results[key] = {"status": "skipped"}
                yield _sse_event("diff_progress", {"current": i + 1, "total": total, "key": key, "status": "skipped"})
                continue

            try:
                if entry.parent_params:
                    unsupported_params = [p for p in entry.parent_params if p not in PARENT_RESOLVERS]
                    if unsupported_params:
                        results[key] = {"status": "unsupported", "error": f"Cannot resolve: {unsupported_params}"}
                        yield _sse_event("diff_progress", {"current": i + 1, "total": total, "key": key, "status": "unsupported"})
                        continue

                    from itertools import product as iterproduct
                    target_objects = []
                    param_values = []
                    for param in entry.parent_params:
                        values = await resolve_parent_values(target_client, req.target_site_id, param)
                        param_values.append([(param, v) for v in values])

                    if all(param_values):
                        for combo in iterproduct(*param_values):
                            purl = entry.list_path.replace("{omadacId}", target_client._omadac_id).replace("{siteId}", req.target_site_id)
                            for pn, pv in combo:
                                purl = purl.replace("{" + pn + "}", pv)
                            full_url = f"{target_client._base_url}{purl}"
                            try:
                                extra = READ_EXTRA_PARAMS.get(key)
                                if entry.is_list:
                                    items = await target_client.get_paginated(full_url, extra_params=extra)
                                else:
                                    item = await target_client.get_singleton(full_url, extra_params=extra)
                                    items = [item] if item else []
                                for obj in items:
                                    obj["_parents"] = dict(combo)
                                target_objects.extend(items)
                            except OmadaApiError:
                                pass
                    else:
                        target_objects = []
                else:
                    url = entry.list_path.replace("{omadacId}", target_client._omadac_id).replace("{siteId}", req.target_site_id)
                    full_url = f"{target_client._base_url}{url}"
                    extra = READ_EXTRA_PARAMS.get(key)
                    if entry.is_list:
                        target_objects = await target_client.get_paginated(full_url, extra_params=extra)
                    else:
                        obj = await target_client.get_singleton(full_url, extra_params=extra)
                        target_objects = [obj] if obj else []

                diff = diff_resource(src_data["objects"], target_objects)
                results[key] = {"status": "ok", **diff}
                yield _sse_event("diff_progress", {"current": i + 1, "total": total, "key": key, "status": "ok"})
            except OmadaApiError as e:
                status = "unsupported" if e.is_unsupported else "error"
                results[key] = {"status": status, "error": str(e)}
                yield _sse_event("diff_progress", {"current": i + 1, "total": total, "key": key, "status": status})
            except Exception as e:
                results[key] = {"status": "error", "error": str(e)}
                yield _sse_event("diff_progress", {"current": i + 1, "total": total, "key": key, "status": "error"})

        yield _sse_event("diff_done", results)

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Write / Execute (FR-6, FR-7, FR-8, FR-9) ---

class WriteRequest(BaseModel):
    target_profile: str
    target_site_id: str
    operations: list[dict[str, Any]]


@app.post("/api/plan")
async def create_plan(req: WriteRequest):
    """Build a write plan from selected operations (FR-8.1)."""
    registry = get_registry()
    target_client = await get_connection(req.target_profile)
    plan_items = []

    for op in req.operations:
        entry = registry.get(op["resource_key"])
        if not entry:
            continue

        op_type = OpType(op["op_type"])
        payload = op.get("payload", {})

        # Apply quirks
        payload = apply_write_quirks(op["resource_key"], payload)

        # Determine URL
        if op_type == OpType.CREATE and entry.create_path:
            url = entry.create_path
        elif op_type == OpType.UPDATE and entry.update_path:
            url = entry.update_path.replace("{id}", op.get("target_id", ""))
        elif op_type == OpType.DELETE and entry.delete_path:
            url = entry.delete_path.replace("{id}", op.get("target_id", ""))
        else:
            continue

        url = url.replace("{omadacId}", target_client._omadac_id).replace("{siteId}", req.target_site_id)
        full_url = f"{target_client._base_url}{url}"

        plan_items.append({
            "op_type": op_type.value,
            "resource_key": op["resource_key"],
            "object_name": op.get("object_name", "unnamed"),
            "url": full_url,
            "payload": payload,
        })

    return {"plan": plan_items}


import re as _re
_PATH_PARAM_RE = _re.compile(r"\{([^}]+)\}")


def _replace_item_id(path: str, target_id: str) -> str:
    """Replace the item-level {param} in a URL path with the target_id.

    Skips known structural params (omadacId, siteId) and parent params
    (apMac, switchMac, etc.). Replaces the FIRST remaining {param} that
    looks like an item identifier.
    """
    if not target_id:
        return path
    structural = {"omadacId", "siteId", "apMac", "switchMac", "gatewayMac",
                  "deviceMac", "stackId", "wlanId", "portalId", "profileId"}
    for m in _PATH_PARAM_RE.finditer(path):
        param = m.group(1)
        if param not in structural:
            return path[:m.start()] + target_id + path[m.end():]
    return path


@app.post("/api/execute")
async def execute_write_plan(req: WriteRequest):
    """Execute a confirmed write plan with SSE progress (FR-8.3)."""

    async def generate() -> AsyncGenerator[str, None]:
        target_client = await get_connection(req.target_profile)
        registry = get_registry()
        mapper = IdMapper()

        # Trigger backup on target before writing
        yield _sse_event("progress", {"current": 0, "total": 0, "status": "backup", "name": "Backing up target controller..."})
        try:
            backup_url = f"{target_client._base_url}/openapi/v1/{target_client._omadac_id}/maintenance/backup/self-server"
            await target_client.request("POST", backup_url, json={"retainUser": True})
            # Poll until file appears (max 30s)
            import asyncio as _aio
            files_url = f"{target_client._base_url}/openapi/v1/{target_client._omadac_id}/maintenance/backup/files"
            for _ in range(6):
                await _aio.sleep(5)
                try:
                    resp = await target_client.request("GET", files_url)
                    files = resp.get("result", {}).get("fileList", [])
                    if files:
                        yield _sse_event("progress", {"current": 0, "total": 0, "status": "backup_done", "name": f"Backup saved: {files[0]['fileName']}"})
                        break
                except Exception:
                    pass
        except Exception as e:
            yield _sse_event("progress", {"current": 0, "total": 0, "status": "backup_warn", "name": f"Backup skipped: {e}"})

        # ponytail: resolve parent param values on target for nested creates
        _parent_cache: dict[str, list[str]] = {}

        async def resolve_target_parent(param: str) -> str | None:
            if param not in _parent_cache:
                _parent_cache[param] = await resolve_parent_values(
                    target_client, req.target_site_id, param
                )
            values = _parent_cache[param]
            return values[0] if values else None

        ops_list: list[WriteOp] = []
        for op in req.operations:
            entry = registry.get(op["resource_key"])
            if not entry:
                continue

            op_type = OpType(op["op_type"])
            payload = apply_write_quirks(op["resource_key"], op.get("payload", {}))
            # Stash target_id for split_writes URL construction
            if op.get("target_id"):
                payload["_target_id"] = op["target_id"]

            if op_type == OpType.CREATE and entry.create_path:
                url = entry.create_path
            elif op_type == OpType.UPDATE and entry.update_path:
                url = _replace_item_id(entry.update_path, op.get("target_id", ""))
            elif op_type == OpType.DELETE and entry.delete_path:
                url = _replace_item_id(entry.delete_path, op.get("target_id", ""))
            else:
                continue

            url = url.replace("{omadacId}", target_client._omadac_id).replace("{siteId}", req.target_site_id)

            # Resolve remaining parent params (e.g. {wlanId}) from target
            if entry.parent_params:
                for param in entry.parent_params:
                    placeholder = "{" + param + "}"
                    if placeholder in url:
                        val = await resolve_target_parent(param)
                        if val:
                            url = url.replace(placeholder, val)

            full_url = f"{target_client._base_url}{url}"

            ops_list.append(WriteOp(
                op_type=op_type,
                resource_key=op["resource_key"],
                object_name=op.get("object_name", "unnamed"),
                payload=payload,
                url=full_url,
                source_id=op.get("source_id"),
            ))

        for op in req.operations:
            if op.get("source_id") and op.get("target_id"):
                mapper.add(op["resource_key"], op["source_id"], op["target_id"])

        total = len(ops_list)
        yield _sse_event("progress", {"current": 0, "total": total, "status": "starting"})

        # Retry-until-stable with progress
        queue = list(ops_list)
        succeeded = 0
        failures: list[dict[str, Any]] = []
        pass_num = 0

        while queue:
            pass_num += 1
            next_queue: list[WriteOp] = []
            progress = False

            for write_op in queue:
                try:
                    clean_payload = {k: v for k, v in write_op.payload.items() if not k.startswith("_")}
                    remapped, unresolved = remap_references(clean_payload, mapper)
                    if unresolved:
                        raise UnresolvedRefError(unresolved[0])

                    if write_op.op_type == OpType.CREATE:
                        result = await target_client.create(write_op.url, remapped)
                    elif write_op.op_type == OpType.UPDATE:
                        # Check for split writes
                        entry = registry.get(write_op.resource_key)
                        if entry and entry.split_writes:
                            splits = split_payload_for_writes(remapped, entry.split_writes)
                            result = {}
                            split_ok = 0
                            split_errors = []
                            for ep_path, sub_payload in splits:
                                ep_url = ep_path.replace("{omadacId}", target_client._omadac_id).replace("{siteId}", req.target_site_id)
                                if entry.parent_params:
                                    for param in entry.parent_params:
                                        placeholder = "{" + param + "}"
                                        if placeholder in ep_url:
                                            val = await resolve_target_parent(param)
                                            if val:
                                                ep_url = ep_url.replace(placeholder, val)
                                target_id = write_op.payload.get("_target_id", "")
                                if "{ssidId}" in ep_url:
                                    ep_url = ep_url.replace("{ssidId}", target_id)
                                if "{switchMac}" in ep_url:
                                    ep_url = ep_url.replace("{switchMac}", write_op.payload.get("switchMac", ""))
                                ep_url = _PATH_PARAM_RE.sub(lambda m: target_id if m.group(1) not in {"omadacId", "siteId"} else m.group(0), ep_url)
                                full_ep = f"{target_client._base_url}{ep_url}"
                                try:
                                    await target_client.patch(full_ep, sub_payload)
                                    split_ok += 1
                                except Exception as split_err:
                                    ep_name = ep_path.rsplit("/", 1)[-1]
                                    split_errors.append(f"{ep_name}: {split_err}")
                            if split_errors:
                                raise Exception(f"{split_ok}/{split_ok+len(split_errors)} sub-writes ok; failed: {'; '.join(split_errors)}")
                        else:
                            result = await target_client.update(write_op.url, remapped)
                    elif write_op.op_type == OpType.DELETE:
                        result = await target_client.delete(write_op.url)
                        result = {}
                    else:
                        result = {}

                    succeeded += 1
                    progress = True
                    if write_op.op_type == OpType.CREATE and write_op.source_id and isinstance(result, dict) and "id" in result:
                        mapper.add(write_op.resource_key, write_op.source_id, result["id"])

                    yield _sse_event("progress", {
                        "current": succeeded, "total": total, "pass": pass_num,
                        "op": write_op.op_type.value, "name": write_op.object_name, "result": "ok",
                    })
                except UnresolvedRefError:
                    next_queue.append(write_op)
                except Exception as e:
                    failures.append({"name": write_op.object_name, "reason": str(e)})
                    progress = True
                    yield _sse_event("progress", {
                        "current": succeeded, "total": total, "pass": pass_num,
                        "op": write_op.op_type.value, "name": write_op.object_name, "result": "error", "error": str(e),
                    })

                # Rate limit: avoid overwhelming the controller
                import asyncio as _aio2
                await _aio2.sleep(0.2)

            if not progress:
                for op in next_queue:
                    failures.append({"name": op.object_name, "reason": "Unresolved reference after convergence"})
                break
            queue = next_queue

        yield _sse_event("done", {"succeeded": succeeded, "failed": len(failures), "failures": failures})

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Device Migration (FR-10) ---


class DeviceForgetRequest(BaseModel):
    profile_name: str
    site_id: str
    device_mac: str


class DeviceAdoptRequest(BaseModel):
    profile_name: str
    site_id: str
    device_macs: list[str]
    username: str = ""
    password: str = ""


@app.get("/api/devices/{profile_name}/{site_id}")
async def list_devices(profile_name: str, site_id: str):
    """List adopted devices on a controller/site."""
    client = await get_connection(profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{site_id}/devices"
    try:
        items = await client.get_paginated(url)
        return {"devices": items}
    except OmadaApiError as e:
        raise HTTPException(500, str(e))


@app.get("/api/devices/{profile_name}/{site_id}/pending")
async def list_pending_devices(profile_name: str, site_id: str):
    """List pending/adoptable devices on target."""
    client = await get_connection(profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{site_id}/grid/devices/pending"
    try:
        items = await client.get_paginated(url)
        return {"devices": items}
    except OmadaApiError as e:
        if e.is_unsupported:
            return {"devices": []}
        raise HTTPException(500, str(e))


# ponytail: these error codes still mean the device is freed from the controller
# -39055: "forgotten but failed to reset" (device released, reset command didn't reach it)
# -39013: "device does not exist" (already forgotten/pending)
_FORGET_OK_CODES = (-39055, -39013)


@app.post("/api/devices/forget")
async def forget_device(req: DeviceForgetRequest):
    """Forget a device from its current controller."""
    client = await get_connection(req.profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{req.site_id}/devices/{req.device_mac}/forget"
    try:
        result = await client.request("POST", url)
        return {"status": "ok", "result": result.get("result")}
    except OmadaApiError as e:
        if e.error_code in _FORGET_OK_CODES:
            return {"status": "ok", "result": "forgotten (device reset skipped)"}
        raise HTTPException(500, str(e))


@app.get("/api/devices/{profile_name}/{site_id}/device-account")
async def get_device_account(profile_name: str, site_id: str):
    """Get the device SSH credentials for a site (needed for cross-controller adopt)."""
    client = await get_connection(profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{site_id}/device-account"
    try:
        result = await client.get_singleton(url)
        return {"status": "ok", "account": result}
    except OmadaApiError as e:
        if e.is_unsupported:
            return {"status": "ok", "account": None}
        raise HTTPException(500, str(e))


class DeviceAccountUpdate(BaseModel):
    profile_name: str
    site_id: str
    username: str
    password: str


@app.put("/api/devices/device-account")
async def update_device_account(req: DeviceAccountUpdate):
    """Set the device SSH credentials on a controller/site."""
    client = await get_connection(req.profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{req.site_id}/device-account"
    try:
        result = await client.update(url, {"username": req.username, "password": req.password})
        return {"status": "ok", "result": result}
    except OmadaApiError as e:
        raise HTTPException(500, str(e))


@app.post("/api/devices/adopt")
async def adopt_devices(req: DeviceAdoptRequest):
    """Adopt devices on target controller (batch)."""
    client = await get_connection(req.profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{req.site_id}/cmd/devices/batch-adopt"
    payload: dict[str, Any] = {"macs": req.device_macs}
    if req.username:
        payload["username"] = req.username
    if req.password:
        payload["password"] = req.password
    try:
        result = await client.request("POST", url, json=payload)
        return {"status": "ok", "result": result.get("result")}
    except OmadaApiError as e:
        raise HTTPException(500, str(e))


@app.get("/api/devices/{profile_name}/{site_id}/adopt-result/{device_mac}")
async def get_adopt_result(profile_name: str, site_id: str, device_mac: str):
    """Poll adoption result for a device."""
    client = await get_connection(profile_name)
    url = f"{client._base_url}/openapi/v1/{client._omadac_id}/sites/{site_id}/devices/{device_mac}/adopt-result"
    try:
        result = await client.request("GET", url)
        return {"status": "ok", "result": result.get("result")}
    except OmadaApiError as e:
        raise HTTPException(500, str(e))


# --- Resource registry info ---

@app.get("/api/resources")
def list_resources():
    registry = get_registry()
    return [
        {"key": e.key, "is_list": e.is_list, "has_create": e.create_path is not None,
         "has_update": e.update_path is not None, "has_delete": e.delete_path is not None,
         "nested": e.parent_params is not None, "parent_params": e.parent_params}
        for e in registry.entries()
    ]


# --- Static UI ---

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Omada Migrator API running. UI not found at /static/index.html"}
