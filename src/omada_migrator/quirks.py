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

# Resources that must never be migrated (runtime state, hardware-specific, or query-only POST endpoints)
# - clients: POST /v2/.../clients is a query endpoint (takes page/pageSize), not a create
# - internet/ports-config: WAN port hardware layout is controller-specific
# - setting/wan-ports: hardware-specific WAN port assignment
# - vpn/users: VPN user credentials with hashed passwords that can't be migrated
SKIP_RESOURCES: set[str] = {
    "clients",              # query-POST endpoint, not a create (page/pageSize required)
    "setting/wan-ports",    # WAN port UUID assignment is hardware-specific (portUuids are non-transferable)
}


class SkipResource(Exception):
    """Raised by apply_write_quirks to signal this op should be skipped gracefully."""
    pass


def apply_write_quirks(resource_key: str, payload: Any) -> dict[str, Any]:
    """Apply resource-specific payload transformations before write."""
    if not isinstance(payload, dict):
        return {}
    result = dict(payload)

    # LAN networks: strip controller-specific port binding and VLAN mode==0 fields
    if "lan/networks" in resource_key or "lan-networks" in resource_key:
        # interfaceIds are gateway LAN port IDs specific to the source controller's hardware
        # Stripping lets the target assign its own ports (avoids -33568 LAN interface mismatch)
        result.pop("interfaceIds", None)
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
        # pmfMode=0 invalid on fusion; pmfMode=1 (mandatory) requires WPA3 — downgrade to capable for WPA2
        if result.get("pmfMode") == 0:
            result["pmfMode"] = 3
        psk_ver_for_pmf = result.get("pskSetting", {}).get("versionPsk", 0) if isinstance(result.get("pskSetting"), dict) else 0
        if result.get("pmfMode") == 1 and result.get("security") == 3 and psk_ver_for_pmf not in (3, 4):
            result["pmfMode"] = 2  # capable — mandatory PMF requires WPA3
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

    # WAN port config: portIds are hardware-specific (remapped at execute time in app.py);
    # also strip null connection-type sub-objects to avoid 'must not be null' validation errors
    # The target only needs the sub-object matching the protoType (0=static,1=DHCP,2=PPPoE,3=L2TP,4=PPTP)
    _WAN_PROTO_FIELDS = {0: "ipv4Static", 1: "ipv4Dhcp", 2: "ipv4Pppoe", 3: "ipv4L2tp", 4: "ipv4Pptp"}
    _ALL_PROTO_FIELDS = set(_WAN_PROTO_FIELDS.values()) | {"ipv4Pppoa", "ipv4Ipoa"}
    if resource_key == "internet/ports-config":
        wan_ports = result.get("wanPortsConfig")
        if isinstance(wan_ports, list):
            cleaned = []
            for port in wan_ports:
                port = dict(port)
                ipv4 = port.get("wanPortIpv4Setting")
                if isinstance(ipv4, dict):
                    proto_type = ipv4.get("protoType")
                    keep_field = _WAN_PROTO_FIELDS.get(proto_type) if proto_type is not None else None
                    # Strip all proto sub-objects except the one matching protoType; keep all if unknown
                    cleaned_ipv4 = {k: v for k, v in ipv4.items()
                                    if k not in _ALL_PROTO_FIELDS or keep_field is None or k == keep_field}
                    port["wanPortIpv4Setting"] = cleaned_ipv4
                cleaned.append(port)
            result["wanPortsConfig"] = cleaned

    # Port forwarding: WAN port/virtual WAN IDs are controller-specific — strip them
    # so the target uses its default WAN port instead of failing with invalid params
    if "port-forwardings" in resource_key:
        result.pop("interfaceWanPortId", None)
        result.pop("virtualWanId", None)
        result.pop("wanIps", None)
        result.pop("existWanIp", None)
        result.pop("existVirtualWan", None)

    # ACL config mode: mode is required (0=Through profiles, 1=Custom); default to 0 if missing
    if resource_key == "acls/osg-config-mode":
        if result.get("mode") is None:
            result["mode"] = 0

    # VPN site-to-site: siteVpnType is required; default to Auto IPSec (0) if missing
    if "vpn/site-to-site-vpns" in resource_key:
        if result.get("siteVpnType") is None:
            result["siteVpnType"] = 0

    # Portal authTimeout: customTimeout/customTimeoutUnit are required when authTimeout is present
    # but older controllers may export them as null — remove the sub-object to let the target use defaults
    if "portal" in resource_key:
        auth_t = result.get("authTimeout")
        if isinstance(auth_t, dict):
            if auth_t.get("customTimeout") is None or auth_t.get("customTimeoutUnit") is None:
                result.pop("authTimeout", None)

    # Gateway ACL rules with IPv6 source/destination types are not supported on all targets
    # sourceType/destinationType 6=IPv6 Group, 7=IPv6-Port Group
    if "acls/osg-acls" in resource_key or "acls/eap-acls" in resource_key or "acls/osw-acls" in resource_key:
        src_type = result.get("sourceType")
        dst_type = result.get("destinationType")
        if src_type in (6, 7) or dst_type in (6, 7):
            raise SkipResource(f"IPv6 ACL rule (sourceType={src_type}, destinationType={dst_type}) skipped — not supported on this target")

    return result


# ponytail: URL overrides for endpoints where the generic path won't work
_WRITE_URL_OVERRIDES: dict[str, str] = {
    "switches/ports": "multi-ports",
}


def get_write_url_override(resource_key: str) -> str | None:
    """Return an override URL hint for resources needing non-standard write paths."""
    return _WRITE_URL_OVERRIDES.get(resource_key)
