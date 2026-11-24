# ADR-0004: TLS verification on by default

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

The reference Home Assistant integration disables TLS verification for every
controller type, cloud included. This tool may also connect to TP-Link Cloud,
where accepting any certificate exposes credentials to a man in the middle.
This ADR records the decision as it stands in `REQUIREMENTS.md` (FR-11,
rationale 5.7); it was made before ADRs existed.

## Decision

TLS certificate verification is on for every controller connection. A controller
profile can set an explicit self-signed flag (`insecure_tls`) that disables
verification for that connection only.

## Consequences

Local controllers and Fusion gateways with self-signed certificates need the flag
set once per profile. Cloud connections are always verified.

## Alternatives considered

Verification off for all controllers, as in the reference integration: rejected
for the cloud path.
