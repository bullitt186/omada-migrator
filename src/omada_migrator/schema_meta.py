"""Extract human-readable metadata from the OpenAPI spec for UI display."""

import json
import re
from pathlib import Path
from typing import Any

_SITE_PREFIX_RE = re.compile(r"^/openapi/v\d+/\{omadacId\}/sites/\{siteId\}/(.+)$")


class SchemaMeta:
    """Provides human-readable descriptions for resource keys and their fields."""

    def __init__(self, summaries: dict[str, str], field_descs: dict[str, dict[str, str]]):
        self._summaries = summaries
        self._field_descs = field_descs

    @classmethod
    def from_spec(cls, spec_path: Path | str) -> "SchemaMeta":
        spec = json.loads(Path(spec_path).read_text())
        summaries: dict[str, str] = {}
        field_descs: dict[str, dict[str, str]] = {}

        paths = spec.get("paths", {})
        schemas = spec.get("components", {}).get("schemas", {})

        for path, operations in paths.items():
            m = _SITE_PREFIX_RE.match(path)
            if not m:
                continue
            after_site = m.group(1)
            parts = after_site.split("/")
            has_id = parts[-1].startswith("{")
            base_parts = parts[:-1] if has_id else parts
            base_key = "/".join(base_parts)

            # Get summary from GET operation (prefer list over item)
            for method in ("get", "post"):
                op = operations.get(method)
                if op and isinstance(op, dict):
                    summary = op.get("summary", "")
                    if summary and base_key not in summaries:
                        # Clean up: strip "v2" suffix, "Get " prefix for brevity
                        clean = re.sub(r"\s*v\d+$", "", summary)
                        clean = re.sub(r"^(Get|List|Query)\s+", "", clean)
                        summaries[base_key] = clean

                    # Extract field descriptions from request body schema
                    if method in ("post", "put", "patch"):
                        ref = (op.get("requestBody", {})
                               .get("content", {})
                               .get("application/json", {})
                               .get("schema", {})
                               .get("$ref", ""))
                        if ref and base_key not in field_descs:
                            schema_name = ref.split("/")[-1]
                            schema = schemas.get(schema_name, {})
                            props = schema.get("properties", {})
                            descs = {}
                            for fname, finfo in props.items():
                                desc = finfo.get("description", "")
                                if desc:
                                    descs[fname] = desc.split(".")[0]  # first sentence
                            if descs:
                                field_descs[base_key] = descs

            # Also extract from response schema for GET
            get_op = operations.get("get")
            if get_op and isinstance(get_op, dict) and base_key not in field_descs:
                r200 = get_op.get("responses", {}).get("200", {})
                ref = (r200.get("content", {})
                       .get("application/json", {})
                       .get("schema", {})
                       .get("$ref", ""))
                if ref:
                    schema_name = ref.split("/")[-1]
                    # Response wrappers often have a "result" -> "data" -> items ref
                    schema = schemas.get(schema_name, {})
                    # Try to find the actual data schema inside the response wrapper
                    result_prop = schema.get("properties", {}).get("result", {})
                    data_ref = result_prop.get("$ref", "")
                    if not data_ref:
                        data_prop = result_prop.get("properties", {}).get("data", {})
                        items_ref = data_prop.get("items", {}).get("$ref", "")
                        if items_ref:
                            data_ref = items_ref
                    if data_ref:
                        data_schema_name = data_ref.split("/")[-1]
                        data_schema = schemas.get(data_schema_name, {})
                        props = data_schema.get("properties", {})
                        descs = {}
                        for fname, finfo in props.items():
                            desc = finfo.get("description", "")
                            if desc:
                                descs[fname] = desc.split(".")[0]
                        if descs:
                            field_descs[base_key] = descs

        return cls(summaries, field_descs)

    def get_summary(self, resource_key: str) -> str:
        return self._summaries.get(resource_key, "")

    def get_field_descriptions(self, resource_key: str) -> dict[str, str]:
        return self._field_descs.get(resource_key, {})

    def to_dict(self) -> dict[str, Any]:
        """Export all metadata for the frontend."""
        result = {}
        all_keys = set(self._summaries.keys()) | set(self._field_descs.keys())
        for key in all_keys:
            result[key] = {
                "summary": self._summaries.get(key, ""),
                "fields": self._field_descs.get(key, {}),
            }
        return result
