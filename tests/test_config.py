"""Tests for controller profile management (FR-1, FR-10, TD-6)."""

import json
import os
import stat

import pytest

from omada_migrator.config import ConfigStore


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "config.json"


class TestConfigStore:
    def test_save_and_load_profiles(self, config_path):
        store = ConfigStore(config_path)
        profile = {
            "name": "Test Controller",
            "type": "local",
            "url": "https://192.168.1.100:8043",
            "insecure_tls": True,
            "omadac_id": "abc123",
            "client_id": "cid",
            "client_secret": "secret",
        }
        store.add_profile(profile)
        store.save()

        store2 = ConfigStore(config_path)
        store2.load()
        assert len(store2.profiles) == 1
        assert store2.profiles[0]["name"] == "Test Controller"

    def test_file_permissions_600(self, config_path):
        store = ConfigStore(config_path)
        store.add_profile({"name": "x", "type": "local", "url": "https://x", "insecure_tls": False})
        store.save()

        mode = os.stat(config_path).st_mode & 0o777
        assert mode == 0o600

    def test_remove_profile_by_name(self, config_path):
        store = ConfigStore(config_path)
        store.add_profile({"name": "A", "type": "local", "url": "https://a", "insecure_tls": False})
        store.add_profile({"name": "B", "type": "cloud", "url": "https://b", "insecure_tls": False})
        store.remove_profile("A")
        assert len(store.profiles) == 1
        assert store.profiles[0]["name"] == "B"

    def test_update_profile(self, config_path):
        store = ConfigStore(config_path)
        store.add_profile({"name": "C", "type": "local", "url": "https://old", "insecure_tls": False})
        store.update_profile("C", {"url": "https://new"})
        assert store.profiles[0]["url"] == "https://new"

    def test_fusion_profile_shape(self, config_path):
        store = ConfigStore(config_path)
        profile = {
            "name": "Fusion GW",
            "type": "fusion",
            "url": "https://192.168.1.1",
            "insecure_tls": True,
            "username": "admin",
            "password": "secret",
        }
        store.add_profile(profile)
        store.save()

        raw = json.loads(config_path.read_text())
        assert raw[0]["type"] == "fusion"
        assert "username" in raw[0]
        assert "client_id" not in raw[0]

    def test_load_nonexistent_file_returns_empty(self, config_path):
        store = ConfigStore(config_path)
        store.load()
        assert store.profiles == []
