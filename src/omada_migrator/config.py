"""Controller profile storage (FR-1, FR-10, TD-6)."""

import json
import os
from pathlib import Path


class ConfigStore:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self.profiles: list[dict] = []

    def load(self) -> None:
        if not self._path.exists():
            self.profiles = []
            return
        self.profiles = json.loads(self._path.read_text())

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self.profiles, indent=2))
        os.chmod(self._path, 0o600)

    def add_profile(self, profile: dict) -> None:
        self.profiles.append(profile)

    def remove_profile(self, name: str) -> None:
        self.profiles = [p for p in self.profiles if p["name"] != name]

    def update_profile(self, name: str, updates: dict) -> None:
        for p in self.profiles:
            if p["name"] == name:
                p.update(updates)
                return

    def get_profile(self, name: str) -> dict | None:
        return next((p for p in self.profiles if p["name"] == name), None)
