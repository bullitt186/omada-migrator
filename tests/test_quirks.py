"""Tests for quirks overrides (TD-3.2)."""

from omada_migrator.quirks import apply_write_quirks


class TestVlanQuirk:
    def test_vlan_mode_0_omits_vlanId_and_customConfig(self):
        """§2.1: VLAN mode==0 must omit vlanId and vlanSetting.customConfig."""
        payload = {
            "name": "Default VLAN",
            "mode": 0,
            "vlanId": 1,
            "vlanSetting": {"customConfig": {"x": 1}, "other": "kept"},
        }
        result = apply_write_quirks("setting/lan/networks", payload)
        assert "vlanId" not in result
        assert "customConfig" not in result.get("vlanSetting", {})
        assert result["vlanSetting"]["other"] == "kept"

    def test_vlan_mode_nonzero_keeps_fields(self):
        payload = {
            "name": "Custom VLAN",
            "mode": 1,
            "vlanId": 100,
            "vlanSetting": {"customConfig": {"x": 1}},
        }
        result = apply_write_quirks("setting/lan/networks", payload)
        assert result["vlanId"] == 100
        assert "customConfig" in result["vlanSetting"]


class TestPortProfileQuirk:
    def test_uses_batch_endpoint(self):
        """§2.1: Always use multi-ports endpoint, not single-port."""
        from omada_migrator.quirks import get_write_url_override

        override = get_write_url_override("switches/ports")
        # Should indicate batch endpoint preferred
        assert override is not None
        assert "multi-ports" in override
