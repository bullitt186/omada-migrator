"""OpenAPI spec management — bundled fallback + user-provided updates.

Controllers don't serve their spec at a public endpoint. The spec is distributed
separately with the controller software. This module manages multiple spec versions
so the tool can use the right one for each controller's firmware.
"""

import json
from pathlib import Path

SPEC_DIR = Path.home() / ".config" / "omada-migrator" / "specs"
BUNDLED_SPEC = Path(__file__).parent / "openapi_spec.json"


def _ensure_dir():
    SPEC_DIR.mkdir(parents=True, exist_ok=True)


def list_specs() -> list[dict[str, str]]:
    """Return available specs: [{name, path, version_info}]."""
    _ensure_dir()
    specs = []

    # Bundled spec is always available
    specs.append({
        "name": "bundled",
        "path": str(BUNDLED_SPEC),
        "label": "Bundled (shipped with tool)",
        "is_bundled": True,
    })

    # User-provided specs (exclude .meta.json sidecars)
    for f in sorted(SPEC_DIR.glob("*.json")):
        if f.stem.endswith(".meta"):
            continue
        meta = _read_spec_meta(f)
        specs.append({
            "name": f.stem,
            "path": str(f),
            "label": meta.get("label", f.stem),
            "is_bundled": False,
        })

    return specs


def get_spec_path(name: str | None = None) -> Path:
    """Get path to spec by name. None = best available (latest user-provided, or bundled)."""
    if name is None:
        user_specs = sorted(f for f in SPEC_DIR.glob("*.json") if not f.stem.endswith(".meta")) if SPEC_DIR.exists() else []
        if user_specs:
            return user_specs[-1]
        return BUNDLED_SPEC
    if name == "bundled":
        return BUNDLED_SPEC
    path = SPEC_DIR / f"{name}.json"
    if path.exists():
        return path
    return BUNDLED_SPEC


def import_spec(source_path: str, label: str | None = None) -> dict[str, str]:
    """Import an OpenAPI spec from a file path. Returns metadata about the imported spec."""
    _ensure_dir()
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Spec file not found: {source_path}")

    data = json.loads(source.read_text())
    if "paths" not in data:
        raise ValueError("Not a valid OpenAPI spec (no 'paths' key)")

    # Derive a name from the spec's info
    info = data.get("info", {})
    version = info.get("version", "unknown")
    path_count = len(data.get("paths", {}))

    # Generate filename from version + path count for uniqueness
    safe_version = version.replace("/", "-").replace(" ", "_")
    name = f"omada_{safe_version}_{path_count}paths"
    dest = SPEC_DIR / f"{name}.json"

    # Write spec + sidecar meta
    dest.write_text(json.dumps(data))
    meta = {"label": label or f"Omada API {version} ({path_count} paths)", "imported_from": str(source)}
    (SPEC_DIR / f"{name}.meta.json").write_text(json.dumps(meta))

    return {"name": name, "path": str(dest), "label": meta["label"], "paths": path_count}


def delete_spec(name: str) -> bool:
    """Delete a user-provided spec."""
    if name == "bundled":
        return False
    path = SPEC_DIR / f"{name}.json"
    meta_path = SPEC_DIR / f"{name}.meta.json"
    if path.exists():
        path.unlink()
        if meta_path.exists():
            meta_path.unlink()
        return True
    return False


def _read_spec_meta(spec_path: Path) -> dict[str, str]:
    """Read sidecar metadata for a spec file."""
    meta_path = spec_path.with_suffix("").with_suffix(".meta.json")
    # ponytail: .json.meta.json pattern
    meta_path2 = spec_path.parent / f"{spec_path.stem}.meta.json"
    for mp in (meta_path2, meta_path):
        if mp.exists():
            try:
                return json.loads(mp.read_text())
            except (json.JSONDecodeError, OSError):
                pass
    return {}
