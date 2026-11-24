# ADR-0002: Resources are discovered from the OpenAPI spec

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

The scope is "everything configurable" on an Omada site, about 1,600 paths in
the Omada OpenAPI spec. A typed class per endpoint does not scale to that. This
ADR records the decision as it stands in `REQUIREMENTS.md` (TD-3, rationale 5.1);
it was made before ADRs existed.

## Decision

At startup the tool parses the bundled OpenAPI spec and builds a registry of
resource types: site-scoped path templates with a GET and at least one write
operation. Read, diff and write are generic and driven by the registry entry and
its JSON schema. Per-resource exceptions (for example VLAN `mode == 0` field
omission, the batch multi-ports endpoint) live in a small hand-maintained quirks
module. Read-only catalogues and non-site paths are not in the registry.

## Consequences

A new resource in a spec update appears without code changes. The tool depends
on the quality of the spec's schemas; where generic behaviour is wrong, a quirk
override is the fix, not a per-resource code path.

## Alternatives considered

Hand-curated resource classes: does not scale to the chosen scope.
