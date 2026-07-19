"""Snapshot save/load (FR-4, TD-5)."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def save_snapshot(data: dict[str, Any], directory: Path | str) -> Path:
    """Save a snapshot to disk. Returns the file path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    data = dict(data)
    data["captured_at"] = datetime.now(timezone.utc).isoformat()

    site_name = _sanitize(data["site"]["name"])
    ctrl_name = _sanitize(data["controller"]["name"])
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{site_name}_{ctrl_name}_{ts}.json"

    path = directory / filename
    path.write_text(json.dumps(data, indent=2))
    return path


def load_snapshot(path: Path | str) -> dict[str, Any]:
    """Load a snapshot from disk."""
    return json.loads(Path(path).read_text())


def _sanitize(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)
