"""Quirks overrides for known API oddities (TD-3.2)."""

from typing import Any

# Extra query params required for specific GET endpoints
READ_EXTRA_PARAMS: dict[str, dict[str, Any]] = {
    "setting/wan-ports": {"function": 0},
}

# Resources misclassified as singletons by the parser that are actually paginated lists
FORCE_LIST_RESOURCES: set[str] = {
    "setting/iot/radio/transmit-power",
    "wired-networks/disable-nats",
    "acls/osg-custom-acls",
}


def apply_write_quirks(resource_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply resource-specific payload transformations before write."""
    result = dict(payload)

    # VLAN mode==0: omit vlanId and vlanSetting.customConfig
    if "lan/networks" in resource_key or "lan-networks" in resource_key:
        if result.get("mode") == 0:
            result.pop("vlanId", None)
            if "vlanSetting" in result and isinstance(result["vlanSetting"], dict):
                result["vlanSetting"] = {
                    k: v for k, v in result["vlanSetting"].items() if k != "customConfig"
                }

    # ponytail: fusion controllers require extra SSID fields that older local controllers don't expose
    if "ssids" in resource_key:
        _SSID_DEFAULTS = {
            "deviceType": 1,  # 1=EAP only, 3=EAP+Gateway
            "enable11r": False,
            "mloEnable": False,
            "hidePwd": False,
            "pmfMode": 3,  # 1=mandatory, 2=capable, 3=disable
        }
        for k, v in _SSID_DEFAULTS.items():
            if k not in result:
                result[k] = v
        # pmfMode=0 invalid on fusion
        if result.get("pmfMode") == 0:
            result["pmfMode"] = 3
        # WPA-Personal requires pskSetting; list endpoints don't return it
        # ponytail: default to WPA2-PSK/Auto with placeholder key, user changes post-migration
        if result.get("security") == 3 and "pskSetting" not in result:
            result["pskSetting"] = {
                "securityKey": "Migrated12345678",
                "versionPsk": 2,
                "encryptionPsk": 1,
                "gikRekeyPskEnable": False,
            }
        # 6GHz (bit 2) requires WPA3-SAE; strip it for WPA2 SSIDs
        band = result.get("band", 0)
        psk_ver = result.get("pskSetting", {}).get("versionPsk", 0) if isinstance(result.get("pskSetting"), dict) else 0
        if (band & 4) and result.get("security") == 3 and psk_ver != 4:
            result["band"] = band & ~4 or 3  # drop 6G bit; fallback to 2.4+5 if nothing left
        # Strip source IDs and internal fields that confuse the target
        result.pop("ssidId", None)
        result.pop("_parents", None)

    return result


# ponytail: URL overrides for endpoints where the generic path won't work
_WRITE_URL_OVERRIDES: dict[str, str] = {
    "switches/ports": "multi-ports",
}


def get_write_url_override(resource_key: str) -> str | None:
    """Return an override URL hint for resources needing non-standard write paths."""
    return _WRITE_URL_OVERRIDES.get(resource_key)
