"""OpenAPI spec parser for resource discovery (TD-3)."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SITE_PREFIX_RE = re.compile(r"^/openapi/v\d+/\{omadacId\}/sites/\{siteId\}/(.+)$")


@dataclass
class ResourceEntry:
    key: str
    list_path: str | None = None
    get_path: str | None = None
    create_path: str | None = None
    update_path: str | None = None
    delete_path: str | None = None
    is_list: bool = True
    schema_ref: str | None = None
    parent_params: list[str] | None = None
    # Split writes: fields that must be written to different endpoints
    # {endpoint_path: [field_names]} — used when update requires multiple calls
    split_writes: dict[str, list[str]] | None = None


class ResourceRegistry:
    def __init__(self):
        self._entries: dict[str, ResourceEntry] = {}

    @classmethod
    def from_spec(cls, spec_path: Path | str) -> "ResourceRegistry":
        spec = json.loads(Path(spec_path).read_text())
        reg = cls()
        reg._parse(spec)
        return reg

    def _parse(self, spec: dict[str, Any]) -> None:
        paths = spec.get("paths", {})
        # Group paths by their base resource key
        grouped: dict[str, dict[str, list[tuple[str, str]]]] = {}  # key -> {method -> [(full_path, ...)]}

        for path, operations in paths.items():
            m = SITE_PREFIX_RE.match(path)
            if not m:
                continue
            after_site = m.group(1)
            parts = after_site.split("/")

            # Base key: strip only the *last* segment if it's a {param}
            # Middle {params} (like {wlanId}) are kept as part of the key
            has_id = parts[-1].startswith("{")
            if has_id:
                base_parts = parts[:-1]
            else:
                base_parts = parts
            base_key = "/".join(base_parts)

            if base_key not in grouped:
                grouped[base_key] = {"paths_info": []}

            for method in ("get", "post", "put", "patch", "delete"):
                if method in operations:
                    grouped[base_key]["paths_info"].append((method, path, has_id, operations[method]))

        for key, info in grouped.items():
            methods_on_base = set()
            methods_on_id = set()
            entry = ResourceEntry(key=key)

            for method, path, has_id, op_data in info["paths_info"]:
                if has_id:
                    methods_on_id.add(method)
                else:
                    methods_on_base.add(method)

                if method == "get" and not has_id:
                    entry.list_path = path
                elif method == "get" and has_id:
                    entry.get_path = path
                elif method == "post" and not has_id:
                    entry.create_path = path
                elif method in ("put", "patch") and has_id:
                    entry.update_path = path
                elif method in ("put", "patch") and not has_id:
                    # Singleton update
                    entry.update_path = path
                elif method == "delete" and has_id:
                    entry.delete_path = path

                # Extract schema ref from request body
                if method in ("post", "put", "patch") and not entry.schema_ref:
                    rb = op_data.get("requestBody", {})
                    content = rb.get("content", {})
                    for ct, schema_info in content.items():
                        ref = schema_info.get("schema", {}).get("$ref")
                        if ref:
                            entry.schema_ref = ref
                            break

            # Determine if list or singleton
            if entry.list_path and not methods_on_id and "post" not in methods_on_base:
                entry.is_list = False
            elif not entry.list_path and entry.get_path:
                entry.is_list = False
                entry.list_path = entry.get_path

            # Detect nested resources with unresolved parent params
            read_path = entry.list_path or entry.get_path or ""
            remaining = read_path.replace("{omadacId}", "").replace("{siteId}", "")
            parent_params = re.findall(r"\{(\w+)\}", remaining)
            if parent_params:
                entry.parent_params = parent_params

            self._entries[key] = entry

        # Merge singular/plural mismatches before pruning
        self._fix_plural_mismatches()

        # Fix misclassified singletons that are actually paginated lists
        from .quirks import FORCE_LIST_RESOURCES
        for key in FORCE_LIST_RESOURCES:
            if key in self._entries:
                self._entries[key].is_list = True

        # Prune: keep only entries that have both read AND write
        self._entries = {
            k: e for k, e in self._entries.items()
            if (e.list_path or e.get_path) and (e.create_path or e.update_path or e.delete_path)
        }

        # Add manual entries AFTER prune (these define their own read+write)
        self._add_manual_entries()

    def _fix_plural_mismatches(self) -> None:
        """Merge entries where GET is on plural path and PUT/DELETE on singular.

        e.g. GET time-range-profiles + PUT time-range-profile/{id}
        """
        # Collect write-only entries (have write but no read)
        write_only = {k: e for k, e in self._entries.items()
                      if (e.update_path or e.delete_path) and not e.list_path and not e.get_path}
        # Collect entries that have a read
        read_entries = {k: e for k, e in self._entries.items()
                       if (e.list_path or e.get_path)}

        for wk, we in list(write_only.items()):
            if wk not in self._entries:
                continue
            # Generate plural candidates from the last path segment
            parts = wk.rsplit("/", 1)
            last = parts[-1] if len(parts) > 1 else wk
            prefix = parts[0] + "/" if len(parts) > 1 else ""

            candidates = [
                prefix + last + "s",       # profile → profiles
                prefix + last + "es",      # match → matches
                wk + "s",                  # full key + s
            ]
            # Also handle "foo-bar" where plural is "foo-bars"
            if "-" in last:
                candidates.append(prefix + last + "s")

            for candidate in candidates:
                if candidate in read_entries:
                    re_entry = read_entries[candidate]
                    if we.update_path and not re_entry.update_path:
                        re_entry.update_path = we.update_path
                    if we.delete_path and not re_entry.delete_path:
                        re_entry.delete_path = we.delete_path
                    del self._entries[wk]
                    break

    def _add_manual_entries(self) -> None:
        """Add resources whose read/write paths don't share a base (quirks).

        These cover the cases where the OpenAPI spec has the read on one path
        and the write on a structurally different path. The spec parser can't
        infer these relationships, so they're hand-maintained per TD-3.2.
        """
        V1 = "/openapi/v1/{omadacId}/sites/{siteId}"

        # Switch ports: flat read via overview, per-switch batch write
        self._entries["switches/ports/config"] = ResourceEntry(
            key="switches/ports/config",
            list_path=f"{V1}/switches/ports/overview",
            update_path=f"{V1}/switches/{{switchMac}}/multi-ports/config",
            is_list=True,
        )
        # Gateway port config: flat read, per-gateway write
        self._entries["gateways/ports/config"] = ResourceEntry(
            key="gateways/ports/config",
            list_path=f"{V1}/internet/ports-config",
            update_path=f"{V1}/gateways/{{gatewayMac}}/multi-ports/config",
            is_list=False,
        )
        # MAC filters: GET on sub-paths (deny/allow), write on base/{id}
        self._entries["mac-filters"] = ResourceEntry(
            key="mac-filters",
            list_path=f"{V1}/mac-filters/deny",
            create_path=f"{V1}/mac-filters",
            update_path=f"{V1}/mac-filters/{{filterId}}",
            delete_path=f"{V1}/mac-filters/{{filterId}}",
            is_list=True,
        )
        # URL filters: GET on sub-paths, write on base
        self._entries["url-filters"] = ResourceEntry(
            key="url-filters",
            list_path=f"{V1}/url-filters/gateway",
            create_path=f"{V1}/url-filters",
            update_path=f"{V1}/url-filters/{{ruleId}}",
            delete_path=f"{V1}/url-filters/{{ruleId}}",
            is_list=True,
        )
        # Network mapping
        self._entries["network-mapping"] = ResourceEntry(
            key="network-mapping",
            list_path=f"{V1}/network-mapping",
            update_path=f"{V1}/network-mapping",
            is_list=False,
        )
        # WAN port setting
        self._entries["wan/networks/port-setting"] = ResourceEntry(
            key="wan/networks/port-setting",
            list_path=f"{V1}/wan/networks/port-setting",
            update_path=f"{V1}/wan/networks/port-setting",
            is_list=False,
        )
        # Hotspot setting
        self._entries["hotspot/setting"] = ResourceEntry(
            key="hotspot/setting",
            list_path=f"{V1}/hotspot/setting",
            update_path=f"{V1}/hotspot/setting",
            is_list=False,
        )
        # RF Planning config
        self._entries["rfPlanning/config"] = ResourceEntry(
            key="rfPlanning/config",
            list_path=f"{V1}/rfPlanning/config",
            update_path=f"{V1}/rfPlanning/config",
            is_list=False,
        )
        # RF Planning excluded APs
        self._entries["rfPlanning/excludeAps"] = ResourceEntry(
            key="rfPlanning/excludeAps",
            list_path=f"{V1}/rfPlanning/excludeAps",
            update_path=f"{V1}/rfPlanning/excludeAps",
            is_list=False,
        )

        # SSID: no generic PUT, split across update-* endpoints
        ssid_entry = self._entries.get("wireless-network/wlans/{wlanId}/ssids")
        if ssid_entry:
            ssid_base = f"{V1}/wireless-network/wlans/{{wlanId}}/ssids/{{ssidId}}"
            if not ssid_entry.update_path:
                ssid_entry.update_path = f"{ssid_base}/update-basic-config"
            ssid_entry.split_writes = {
                f"{ssid_base}/update-basic-config": [
                    "name", "band", "autoWanAccess", "guestNetEnable", "security", "oweEnable",
                    "broadcast", "vlanEnable", "vlanId", "vlanSetting", "pskSetting", "entSetting",
                    "ppskSetting", "mloEnable", "pmfMode", "enable11r", "hidePwd", "greEnable",
                    "prohibitWifiShare",
                ],
                f"{ssid_base}/update-rate-limit": ["clientRateLimit", "ssidRateLimit"],
                f"{ssid_base}/update-mac-filter": ["macFilterEnable", "policy", "macFilterId", "ouiProfileIdList"],
                f"{ssid_base}/update-wlan-schedule": ["wlanScheduleEnable", "action", "scheduleId"],
                f"{ssid_base}/update-rate-control": [
                    "rate2gCtrlEnable", "lowerDensity2g", "higherDensity2g", "cckRatesDisable",
                    "rate5gCtrlEnable", "lowerDensity5g", "higherDensity5g", "rate6gCtrlEnable",
                    "lowerDensity6g", "higherDensity6g", "sendBeacons2g", "sendBeacons5g",
                    "clientRatesRequire2g", "clientRatesRequire5g", "clientRatesRequire6g",
                    "manageRateControl2g", "manageRateControl2gEnable",
                    "manageRateControl5g", "manageRateControl5gEnable",
                ],
                f"{ssid_base}/update-multicast-config": [
                    "multiCastEnable", "channelUtil", "arpCastEnable", "ipv6CastEnable",
                    "filterEnable", "filterMode", "macGroupId",
                ],
                f"{ssid_base}/update-dhcp-option": ["dhcpEnable", "format", "delimiter", "circuitId", "remoteId"],
                f"{ssid_base}/update-hotspotv2": [
                    "hotspotV2Enable", "networkType", "plmnId", "roamingConsortiumOi",
                    "operatorDomain", "dgafDisable", "heSsid", "internet",
                    "operatorFriendly", "realmList", "venueInfo",
                    "availabilityIpv4", "availabilityIpv6",
                ],
            }

        # Switch ports: split writes for different field groups
        sp_entry = self._entries.get("switches/ports/config")
        if sp_entry:
            sp_base = f"{V1}/switches/{{switchMac}}/multi-ports"
            sp_entry.split_writes = {
                f"{sp_base}/config": [
                    "name", "tagIds", "nativeNetworkId", "nativeBridgeVlan", "networkTagsSetting",
                    "tagNetworkIds", "tagBridgeVlanMap", "untagNetworkIds", "untagBridgeVlanMap",
                    "voiceNetworkEnable", "voiceNetworkId", "voiceBridgeVlan",
                    "voiceDscpEnable", "voiceDscp", "portAlertEnable",
                ],
                f"{sp_base}/poe-mode": ["poeMode"],
                f"{sp_base}/status": ["status"],
                f"{sp_base}/profile-override": ["profileOverrideEnable"],
            }

        # Fix entries where registry read exists but needs additional write paths

        # Switch QoS rule status toggle
        qos = self._entries.get("switch-qos/qos-rule")
        if qos and not qos.update_path:
            qos.update_path = f"{V1}/switch-qos/qos-rule/status/{{qosRuleId}}"

        # Stacks per-stack config
        stacks_entry = self._entries.get("stacks")
        if stacks_entry and not stacks_entry.update_path:
            stacks_entry.update_path = f"{V1}/stacks/{{stackId}}/config"

        # Network security IPS allow-list
        self._entries["network-security/ips/allow-list"] = ResourceEntry(
            key="network-security/ips/allow-list",
            list_path=f"{V1}/network-security/ips",
            update_path=f"{V1}/network-security/ips/allow-list",
            is_list=False,
        )

        # Switch LAGs
        self._entries["switches/{switchMac}/lags"] = ResourceEntry(
            key="switches/{switchMac}/lags",
            list_path=f"{V1}/switches/{{switchMac}}/port-lag-networks",
            update_path=f"{V1}/switches/{{switchMac}}/lags/{{lagId}}",
            is_list=True,
            parent_params=["switchMac"],
        )

        # --- Remaining 19 gaps: pair mismatched reads with writes ---

        # Anomaly setting: read at anomaly/setting, write at anomaly/setting/modify
        self._entries["anomaly/setting"] = ResourceEntry(
            key="anomaly/setting",
            list_path=f"{V1}/anomaly/setting",
            update_path=f"{V1}/anomaly/setting/modify",
            is_list=False,
        )

        # AP sub-configs: read from per-AP detail, write to specific sub-paths
        # These are subset writes of the AP object (already readable via aps/{apMac}/radio-config etc.)
        # but need their own write paths registered
        self._entries["aps/{apMac}/channel-config"] = ResourceEntry(
            key="aps/{apMac}/channel-config",
            list_path=f"{V1}/aps/{{apMac}}/radio-config",
            update_path=f"{V1}/aps/{{apMac}}/channel-config",
            is_list=False,
            parent_params=["apMac"],
        )
        self._entries["aps/{apMac}/service-config"] = ResourceEntry(
            key="aps/{apMac}/service-config",
            list_path=f"{V1}/aps/{{apMac}}/general-config",
            update_path=f"{V1}/aps/{{apMac}}/service-config",
            is_list=False,
            parent_params=["apMac"],
        )
        self._entries["aps/{apMac}/wlan-group"] = ResourceEntry(
            key="aps/{apMac}/wlan-group",
            list_path=f"{V1}/aps/{{apMac}}/general-config",
            update_path=f"{V1}/aps/{{apMac}}/wlan-group",
            is_list=False,
            parent_params=["apMac"],
        )

        # Gateway internet config: read from gateway internet, write to sub-paths
        self._entries["gateways/{gatewayMac}/internet/lte/ports-config"] = ResourceEntry(
            key="gateways/{gatewayMac}/internet/lte/ports-config",
            list_path=f"{V1}/internet/lte/ports-config",
            update_path=f"{V1}/gateways/{{gatewayMac}}/internet/lte/ports-config",
            is_list=False,
            parent_params=["gatewayMac"],
        )
        self._entries["gateways/{gatewayMac}/internet/wan-mode"] = ResourceEntry(
            key="gateways/{gatewayMac}/internet/wan-mode",
            list_path=f"{V1}/internet/ports-config",
            update_path=f"{V1}/gateways/{{gatewayMac}}/internet/wan-mode",
            is_list=False,
            parent_params=["gatewayMac"],
        )

        # Hotspot voucher group pattern
        self._entries["hotspot/voucher-groups/pattern"] = ResourceEntry(
            key="hotspot/voucher-groups/pattern",
            list_path=f"{V1}/hotspot/voucher-groups",
            update_path=f"{V1}/hotspot/voucher-groups/{{groupId}}/pattern",
            is_list=True,
        )

        # Profiles groups (per group-type, per group)
        self._entries["profiles/groups"] = ResourceEntry(
            key="profiles/groups",
            list_path=f"{V1}/profiles/groups",
            update_path=f"{V1}/profiles/groups/{{groupType}}/{{groupId}}",
            is_list=True,
        )

        # Report tabs
        self._entries["report/tabs"] = ResourceEntry(
            key="report/tabs",
            list_path=f"{V1}/report/allTabs",
            update_path=f"{V1}/report/tab",
            is_list=True,
        )

        # VoIP call forwarding
        self._entries["setting/voip/call-forwarding"] = ResourceEntry(
            key="setting/voip/call-forwarding",
            list_path=f"{V1}/setting/voip/call-forwarding/grid",
            update_path=f"{V1}/setting/voip/call-forwarding",
            is_list=True,
        )

        # VoIP provider profiles
        self._entries["setting/voip/provider-profiles"] = ResourceEntry(
            key="setting/voip/provider-profiles",
            list_path=f"{V1}/setting/voip/grid/provider-profiles",
            update_path=f"{V1}/setting/voip/provider-profiles/{{profileId}}",
            is_list=True,
        )

        # VoIP devices batch modify
        self._entries["setting/voip/voip-devices/config"] = ResourceEntry(
            key="setting/voip/voip-devices/config",
            list_path=f"{V1}/setting/voip/voip-devices",
            update_path=f"{V1}/setting/voip/voip-devices/batch-modify",
            is_list=True,
        )

        # VoIP devices per-gateway
        self._entries["setting/voip/voip-devices/osg"] = ResourceEntry(
            key="setting/voip/voip-devices/osg",
            list_path=f"{V1}/setting/voip/voip-devices",
            update_path=f"{V1}/setting/voip/voip-devices/osg/{{deviceMac}}",
            is_list=True,
            parent_params=["deviceMac"],
        )

        # Stacks per-stack config and loopback
        self._entries["stacks/{stackId}/config"] = ResourceEntry(
            key="stacks/{stackId}/config",
            list_path=f"{V1}/stacks/{{stackId}}",
            update_path=f"{V1}/stacks/{{stackId}}/config",
            is_list=False,
            parent_params=["stackId"],
        )
        self._entries["stacks/{stackId}/config/loopback"] = ResourceEntry(
            key="stacks/{stackId}/config/loopback",
            list_path=f"{V1}/stacks/{{stackId}}",
            update_path=f"{V1}/stacks/{{stackId}}/config/loopback",
            is_list=False,
            parent_params=["stackId"],
        )
        # Stacks multi-ports config (batch port settings per stack)
        self._entries["stacks/{stackId}/multi-ports/config"] = ResourceEntry(
            key="stacks/{stackId}/multi-ports/config",
            list_path=f"{V1}/stacks/{{stackId}}/ports",
            update_path="/openapi/v2/{omadacId}/sites/{siteId}/stacks/{stackId}/multi-ports/config",
            is_list=True,
            parent_params=["stackId"],
        )

        # Dashboard batch tab config (different from per-tab update)
        self._entries["dashboard/multi-tabs/config"] = ResourceEntry(
            key="dashboard/multi-tabs/config",
            list_path=f"{V1}/dashboard/tabs",
            update_path=f"{V1}/dashboard/multi-tabs/config",
            is_list=False,
        )

        # Virtual WAN per-item status toggle
        self._entries["setting/virtual-wans/status"] = ResourceEntry(
            key="setting/virtual-wans/status",
            list_path=f"{V1}/setting/virtual-wans",
            update_path=f"{V1}/setting/virtual-wans/{{virtualWanId}}/status",
            is_list=True,
        )

    def resource_keys(self) -> list[str]:
        return list(self._entries.keys())

    def get(self, key: str) -> ResourceEntry | None:
        return self._entries.get(key)

    def entries(self) -> list[ResourceEntry]:
        return list(self._entries.values())
