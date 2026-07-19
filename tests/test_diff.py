"""Tests for the diff engine (FR-5, FR-7)."""

import pytest

from omada_migrator.diff import diff_objects, match_objects, DiffResult, DiffStatus


class TestMatchObjects:
    def test_match_by_name(self):
        source = [{"id": "s1", "name": "Office"}, {"id": "s2", "name": "Guest"}]
        target = [{"id": "t1", "name": "Office"}, {"id": "t3", "name": "Lab"}]
        matched, source_only, target_only = match_objects(source, target, key_fields=["name"])
        assert len(matched) == 1
        assert matched[0] == ({"id": "s1", "name": "Office"}, {"id": "t1", "name": "Office"})
        assert source_only == [{"id": "s2", "name": "Guest"}]
        assert target_only == [{"id": "t3", "name": "Lab"}]

    def test_match_by_ssid_field(self):
        source = [{"ssidId": "a", "ssid": "MyWifi"}]
        target = [{"ssidId": "b", "ssid": "MyWifi"}]
        matched, so, to = match_objects(source, target, key_fields=["ssid", "name"])
        assert len(matched) == 1

    def test_fallback_to_id(self):
        source = [{"id": "x1", "value": 1}]
        target = [{"id": "x1", "value": 2}]
        matched, _, _ = match_objects(source, target, key_fields=["name", "ssid"])
        # Falls back to 'id' matching when no key field found
        assert len(matched) == 1


class TestDiffObjects:
    def test_identical_objects(self):
        src = {"id": "1", "name": "Net", "subnet": "10.0.0.0/24"}
        tgt = {"id": "2", "name": "Net", "subnet": "10.0.0.0/24"}
        result = diff_objects(src, tgt)
        assert result.status == DiffStatus.IDENTICAL

    def test_differing_objects(self):
        src = {"id": "1", "name": "Net", "subnet": "10.0.0.0/24"}
        tgt = {"id": "2", "name": "Net", "subnet": "10.0.1.0/24"}
        result = diff_objects(src, tgt)
        assert result.status == DiffStatus.DIFFERS
        assert "subnet" in result.changed_fields

    def test_ignores_server_assigned_fields(self):
        src = {"id": "1", "name": "Net", "createTime": 1000, "modifyTime": 2000}
        tgt = {"id": "2", "name": "Net", "createTime": 3000, "modifyTime": 4000}
        result = diff_objects(src, tgt)
        assert result.status == DiffStatus.IDENTICAL

    def test_nested_diff(self):
        src = {"id": "1", "name": "A", "config": {"key": "val1"}}
        tgt = {"id": "2", "name": "A", "config": {"key": "val2"}}
        result = diff_objects(src, tgt)
        assert result.status == DiffStatus.DIFFERS


class TestDiffResult:
    def test_full_diff_summary(self):
        source = [
            {"id": "1", "name": "Same", "x": 1},
            {"id": "2", "name": "Changed", "x": 1},
            {"id": "3", "name": "SourceOnly", "x": 1},
        ]
        target = [
            {"id": "a", "name": "Same", "x": 1},
            {"id": "b", "name": "Changed", "x": 99},
            {"id": "c", "name": "TargetOnly", "x": 1},
        ]
        from omada_migrator.diff import diff_resource
        results = diff_resource(source, target, key_fields=["name"])
        assert results["identical"] == 1
        assert results["differs"] == 1
        assert results["source_only"] == 1
        assert results["target_only"] == 1
