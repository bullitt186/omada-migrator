# ADR-0003: Write order by retry-until-stable, not a dependency graph

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Selected objects reference each other by ID (a network used by an SSID, a VLAN
used by a profile). IDs differ between controllers, and the order of writes
matters. This ADR records the decision as it stands in `REQUIREMENTS.md` (FR-7,
FR-9, TD-4, rationale 5.3 and 5.5); it was made before ADRs existed.

## Decision

No dependency graph is built. All selected writes are attempted in a pass; a
write whose reference field has no target ID yet, or that fails with a
dependency-shaped API error, is queued for the next pass. Passes repeat until the
queue is empty or a pass makes no progress; what remains is reported as a
failure with the field and object named. Reference fields are remapped only via a
hand-maintained `{field: resource type}` table; fields not in it are left as they
are and never guessed.

## Consequences

The order is correct by construction whenever some order succeeds. The reference
table starts incomplete and grows as resource types are exercised; an unmapped
reference shows up as an unresolved item for the operator instead of a wrong ID
written to a live controller.

## Alternatives considered

A type-level dependency graph inferred from `*Id` field names: could miss or
misorder references and silently write in a wrong order.
