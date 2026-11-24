# Scope

What this project is for, so contributions go in the same direction. Changing this file is a
maintainer decision, recorded as an ADR.

The full requirements, rationale and verification plan are in
[REQUIREMENTS.md](../REQUIREMENTS.md); this file summarises the scope from it.

## Purpose

A lightweight, locally run tool for migrating configuration from one TP-Link Omada controller
to another (hardware replacement, cloud-to-local migration, adding a Fusion gateway). It reads
all site-scoped settings from a source controller via the Omada OpenAPI, shows them next to the
target's current state, lets the operator review a diff and select what to carry over, and
writes the selection to the target, with snapshots on disk so read and write need not happen in
the same session. It is for operators doing such a migration themselves.

## In scope

- Controller profiles for local, cloud and Fusion controllers (FR-1).
- Site-scoped settings of every resource type in the OpenAPI spec that can be read and written
  (FR-3, ADR-0002).
- Snapshots of a read, diff against a live target, whole-object selection, previewed write plan
  with ID remapping and retry-until-stable execution (FR-4 to FR-9, ADR-0003).

## Out of scope

- Controller- or MSP-level (non-site) settings such as admin accounts: not part of v1.
- Field-level selective writes: payloads mixing source and target fields are not what the API
  expects (REQUIREMENTS.md 5.4).
- A manual mapping UI for renamed objects: natural-key matching covers the common case (5.2).
- Automated or scheduled migrations: an interactive, one-session-at-a-time tool.
- Multi-user access, remote deployment or a login for the tool's own UI: localhost only (5.6).
- Device firmware, live client state, PoE status and other telemetry: settings only.
- Monitoring, or a general Omada API client library.
- Automatic dependency-graph inference for write order: replaced by retry-until-stable (5.3).

## Direction

The v1 definition of done is in REQUIREMENTS.md, section 7.1. CI for the tests: #1. Beyond v1:
Open: to be decided by the maintainer.

## Principles

- Fail safe on a live network controller: an unresolved reference is reported, never guessed
  (ADR-0003).
- Nothing is written before the operator confirms the previewed plan.
- TLS verification on by default (ADR-0004).
- Standard library and existing code first, no speculative abstractions.
