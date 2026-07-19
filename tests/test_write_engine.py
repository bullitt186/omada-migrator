"""Tests for write engine: ID remapping (FR-7) and retry-until-stable (FR-9)."""

import pytest

from omada_migrator.write_engine import (
    WritePlan,
    WriteOp,
    OpType,
    IdMapper,
    remap_references,
    execute_plan,
)


class TestIdMapper:
    def test_add_and_resolve_mapping(self):
        mapper = IdMapper()
        mapper.add("lan-networks", "src_net_1", "tgt_net_1")
        assert mapper.resolve("lan-networks", "src_net_1") == "tgt_net_1"

    def test_unresolved_returns_none(self):
        mapper = IdMapper()
        assert mapper.resolve("lan-networks", "unknown_id") is None


class TestRemapReferences:
    def test_remaps_known_reference_field(self):
        mapper = IdMapper()
        mapper.add("lan-networks", "src_net_1", "tgt_net_1")

        payload = {"name": "MySSID", "networkId": "src_net_1", "band": 0}
        result, unresolved = remap_references(payload, mapper)

        assert result["networkId"] == "tgt_net_1"
        assert unresolved == []

    def test_flags_unresolved_reference(self):
        mapper = IdMapper()
        payload = {"name": "MySSID", "networkId": "src_net_unknown", "band": 0}
        result, unresolved = remap_references(payload, mapper)

        assert result["networkId"] == "src_net_unknown"  # left as-is
        assert "networkId" in unresolved

    def test_non_reference_fields_untouched(self):
        mapper = IdMapper()
        payload = {"name": "Foo", "subnet": "10.0.0.0/24", "vlanId": 100}
        result, unresolved = remap_references(payload, mapper)
        assert result == payload
        assert unresolved == []


class TestSplitPayload:
    def test_splits_fields_to_correct_endpoints(self):
        from omada_migrator.write_engine import split_payload_for_writes

        split_map = {
            "/update-basic-config": ["name", "band", "security"],
            "/update-rate-limit": ["clientRateLimit", "ssidRateLimit"],
        }
        payload = {"name": "MySSID", "band": 0, "clientRateLimit": 100, "ssidRateLimit": 200}
        result = split_payload_for_writes(payload, split_map)

        assert len(result) == 2
        assert result[0] == ("/update-basic-config", {"name": "MySSID", "band": 0})
        assert result[1] == ("/update-rate-limit", {"clientRateLimit": 100, "ssidRateLimit": 200})

    def test_skips_endpoints_with_no_matching_fields(self):
        from omada_migrator.write_engine import split_payload_for_writes

        split_map = {
            "/update-basic-config": ["name", "band"],
            "/update-rate-limit": ["clientRateLimit"],
        }
        payload = {"name": "X", "band": 1}
        result = split_payload_for_writes(payload, split_map)

        assert len(result) == 1
        assert result[0][0] == "/update-basic-config"

    def test_empty_split_map_returns_empty(self):
        from omada_migrator.write_engine import split_payload_for_writes
        assert split_payload_for_writes({"x": 1}, {}) == []
        assert split_payload_for_writes({"x": 1}, None) == []


class TestWritePlan:
    def test_plan_creation(self):
        plan = WritePlan()
        plan.add(WriteOp(
            op_type=OpType.CREATE,
            resource_key="setting/lan/networks",
            object_name="Office LAN",
            payload={"name": "Office LAN", "subnet": "10.0.0.0/24"},
            url="/openapi/v1/omadac/sites/s1/setting/lan/networks",
        ))
        assert len(plan.ops) == 1
        assert plan.ops[0].op_type == OpType.CREATE


class TestRetryUntilStable:
    @pytest.mark.asyncio
    async def test_dependency_chain_resolves_via_convergence(self):
        """FR-9: A->B->C dependency resolves in multiple passes."""
        mapper = IdMapper()

        from omada_migrator.write_engine import UnresolvedRefError

        async def mock_execute(op: WriteOp) -> dict:
            if op.object_name == "Net-A":
                mapper.add("lan-networks", "src_a", "tgt_a")
                return {"id": "tgt_a"}
            elif op.object_name == "Net-B":
                if mapper.resolve("lan-networks", "src_a") is None:
                    raise UnresolvedRefError("networkId")
                mapper.add("lan-networks", "src_b", "tgt_b")
                return {"id": "tgt_b"}
            elif op.object_name == "Net-C":
                if mapper.resolve("lan-networks", "src_b") is None:
                    raise UnresolvedRefError("networkId")
                return {"id": "tgt_c"}
            return {}

        plan = WritePlan()
        # Add in reverse order to test convergence
        plan.add(WriteOp(OpType.CREATE, "lan-networks", "Net-C",
                         {"name": "Net-C", "networkId": "src_b"}, "/url"))
        plan.add(WriteOp(OpType.CREATE, "lan-networks", "Net-B",
                         {"name": "Net-B", "networkId": "src_a"}, "/url"))
        plan.add(WriteOp(OpType.CREATE, "lan-networks", "Net-A",
                         {"name": "Net-A"}, "/url"))

        results = await execute_plan(plan, mock_execute, mapper)
        assert results["succeeded"] == 3
        assert results["failed"] == 0

    @pytest.mark.asyncio
    async def test_genuinely_broken_ref_reported_as_failure(self):
        """FR-9: Unresoluble ref converges to reported failure."""
        mapper = IdMapper()

        async def mock_execute(op: WriteOp) -> dict:
            raise UnresolvedRefError("networkId")

        from omada_migrator.write_engine import UnresolvedRefError

        plan = WritePlan()
        plan.add(WriteOp(OpType.CREATE, "lan-networks", "Broken",
                         {"name": "Broken", "networkId": "never_exists"}, "/url"))

        results = await execute_plan(plan, mock_execute, mapper)
        assert results["succeeded"] == 0
        assert results["failed"] == 1
        assert "networkId" in results["failures"][0]["reason"]
