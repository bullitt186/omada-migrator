"""Tests for resource registry / spec parser (TD-3)."""

from pathlib import Path

import pytest

from omada_migrator.registry import ResourceRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "openapi_mini.json"


class TestResourceRegistry:
    def test_loads_site_scoped_rw_resources(self):
        reg = ResourceRegistry.from_spec(FIXTURE)
        keys = reg.resource_keys()
        # Should include lan/networks, wireless ssids, dhcp setting
        assert "setting/lan/networks" in keys
        assert "wireless-network/wlans/{wlanId}/ssids" in keys
        assert "setting/lan/dhcp" in keys

    def test_excludes_read_only_paths(self):
        reg = ResourceRegistry.from_spec(FIXTURE)
        keys = reg.resource_keys()
        # applicationControl/applications is read-only
        assert "applicationControl/applications" not in keys

    def test_excludes_non_site_scoped(self):
        reg = ResourceRegistry.from_spec(FIXTURE)
        keys = reg.resource_keys()
        assert "devices" not in keys  # the controller-level one

    def test_registry_entry_has_crud_paths(self):
        reg = ResourceRegistry.from_spec(FIXTURE)
        entry = reg.get("setting/lan/networks")
        assert entry is not None
        assert entry.list_path is not None
        assert entry.create_path is not None
        assert entry.update_path is not None
        assert entry.delete_path is not None
        assert entry.is_list is True

    def test_singleton_resource_detected(self):
        reg = ResourceRegistry.from_spec(FIXTURE)
        entry = reg.get("setting/lan/dhcp")
        assert entry is not None
        assert entry.is_list is False

    def test_new_endpoint_in_spec_appears_without_code_change(self):
        """TD-3 verification: adding a path to the spec adds a resource."""
        import json
        import tempfile

        spec = json.loads(FIXTURE.read_text())
        spec["paths"]["/openapi/v1/{omadacId}/sites/{siteId}/setting/new-feature"] = {
            "get": {"operationId": "getNewFeature", "responses": {"200": {}}},
            "put": {"operationId": "updateNewFeature", "responses": {"200": {}}},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(spec, f)
            tmp_path = Path(f.name)

        reg = ResourceRegistry.from_spec(tmp_path)
        assert "setting/new-feature" in reg.resource_keys()
        tmp_path.unlink()
