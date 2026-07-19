"""Tests for snapshot save/load (FR-4, TD-5)."""

import json
from pathlib import Path

import pytest

from omada_migrator.snapshot import save_snapshot, load_snapshot


class TestSnapshot:
    def test_save_creates_file_with_correct_shape(self, tmp_path):
        data = {
            "controller": {"name": "MyCtrl", "type": "local", "url": "https://x"},
            "site": {"site_id": "s1", "name": "Office"},
            "resources": {
                "setting/lan/networks": {
                    "status": "ok",
                    "objects": [{"id": "1", "name": "LAN"}],
                }
            },
        }
        path = save_snapshot(data, tmp_path)
        assert path.exists()
        assert "Office" in path.name
        assert "MyCtrl" in path.name

        loaded = json.loads(path.read_text())
        assert "captured_at" in loaded
        assert loaded["resources"]["setting/lan/networks"]["status"] == "ok"

    def test_load_snapshot(self, tmp_path):
        data = {
            "controller": {"name": "Ctrl", "type": "local", "url": "https://x"},
            "site": {"site_id": "s1", "name": "Home"},
            "captured_at": "2026-07-17T14:30:00Z",
            "resources": {
                "setting/lan/networks": {
                    "status": "ok",
                    "objects": [{"id": "1", "name": "Main"}],
                }
            },
        }
        path = tmp_path / "test_snapshot.json"
        path.write_text(json.dumps(data))

        loaded = load_snapshot(path)
        assert loaded["site"]["name"] == "Home"
        assert loaded["resources"]["setting/lan/networks"]["objects"][0]["name"] == "Main"

    def test_error_resource_stored_correctly(self, tmp_path):
        data = {
            "controller": {"name": "C", "type": "local", "url": "https://x"},
            "site": {"site_id": "s1", "name": "S"},
            "resources": {
                "broken-thing": {
                    "status": "error",
                    "error": "HTTP 500: Internal Server Error",
                    "objects": [],
                },
                "unsupported-thing": {
                    "status": "unsupported",
                    "error": "Endpoint not found (404)",
                    "objects": [],
                },
            },
        }
        path = save_snapshot(data, tmp_path)
        loaded = json.loads(path.read_text())
        assert loaded["resources"]["broken-thing"]["status"] == "error"
        assert loaded["resources"]["unsupported-thing"]["status"] == "unsupported"
