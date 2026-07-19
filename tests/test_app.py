"""Integration tests for FastAPI app (FR-1, FR-12)."""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Create test client with isolated config."""
    config_path = tmp_path / "config.json"
    spec_path = tmp_path / "spec.json"
    # Write minimal spec
    import json
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures" / "openapi_mini.json"
    spec_path.write_text(fixture.read_text())

    with patch("omada_migrator.app.CONFIG_PATH", config_path), \
         patch("omada_migrator.app._spec_path", return_value=spec_path), \
         patch("omada_migrator.app.SNAPSHOT_DIR", tmp_path / "snapshots"):
        # Re-initialize config store with patched path
        from omada_migrator.app import app, config_store
        config_store._path = config_path
        config_store.profiles = []
        yield TestClient(app)


class TestProfileAPI:
    def test_list_empty(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_list(self, client):
        resp = client.post("/api/profiles", json={
            "name": "Test",
            "type": "local",
            "url": "https://192.168.1.1:8043",
            "insecure_tls": True,
            "omadac_id": "oid",
            "client_id": "cid",
            "client_secret": "secret",
        })
        assert resp.status_code == 200

        resp = client.get("/api/profiles")
        profiles = resp.json()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "Test"
        # Secret should be excluded from list
        assert "client_secret" not in profiles[0]

    def test_delete(self, client):
        client.post("/api/profiles", json={
            "name": "ToDelete", "type": "local", "url": "https://x",
        })
        resp = client.delete("/api/profiles/ToDelete")
        assert resp.status_code == 200
        assert client.get("/api/profiles").json() == []


class TestResourcesAPI:
    def test_list_resources(self, client):
        resp = client.get("/api/resources")
        assert resp.status_code == 200
        resources = resp.json()
        assert len(resources) > 0
        assert any(r["key"] == "setting/lan/networks" for r in resources)


class TestSnapshotsAPI:
    def test_list_empty(self, client):
        resp = client.get("/api/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []


class TestDeviceAPI:
    def test_list_devices_no_profile(self, client):
        resp = client.get("/api/devices/nonexistent/siteid")
        assert resp.status_code == 404

    def test_forget_no_profile(self, client):
        resp = client.post("/api/devices/forget", json={
            "profile_name": "nonexistent",
            "site_id": "sid",
            "device_mac": "AA-BB-CC-DD-EE-FF",
        })
        assert resp.status_code == 404

    def test_adopt_no_profile(self, client):
        resp = client.post("/api/devices/adopt", json={
            "profile_name": "nonexistent",
            "site_id": "sid",
            "device_macs": ["AA-BB-CC-DD-EE-FF"],
        })
        assert resp.status_code == 404


class TestUIServing:
    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Omada Migrator" in resp.text
