# Omada Controller Migration Tool — Requirements & Verification

Status: agreed, pre-implementation
Author basis: interview with project owner, 2026-07-17; technical analysis of
`/Users/bullitt/Documents/Repositories/ha-omada-open-api`

## For future Claude

This document is the source of truth for what to build. Every requirement
below traces to a decision made explicitly by the project owner during a
structured interview, or to a fact confirmed by reading the reference HA
integration's source. Where reference-integration behavior is being carried
over as-is, that's noted as "Reference:" and not something to re-litigate.
If an implementation choice isn't covered here, it's an open implementation
detail, not a design gap — use judgment consistent with the ladder in
`ponytail` (stdlib/native first, no speculative abstraction).

---

## 1. Purpose

A lightweight, locally-run tool for migrating configuration from one TP-Link
Omada controller to another (e.g. hardware replacement, cloud-to-local
migration, adding a Fusion gateway). It reads all site-scoped settings from a
source controller via the Omada OpenAPI, displays them alongside the current
state of a target controller, lets the operator review a diff, select what to
carry over, and write the selected changes to the target — with settings
snapshots persisted to disk so a read and a write don't have to happen in the
same session.

Non-goals: this is not a monitoring tool, not a general Omada API client
library, not a multi-tenant/hosted service, and does not manage device
firmware, live client state, or MSP/controller-wide (non-site) settings.

---

## 2. Reference Material

### 2.1 `ha-omada-open-api` — what it establishes as fact

Source: `/Users/bullitt/Documents/Repositories/ha-omada-open-api`,
`custom_components/omada_open_api/{auth.py,api.py,config_flow.py,const.py}`.

**Two authentication strategies, one ABC (`OmadaAuthStrategy`):**

| | Classic (local self-hosted OC300/Software Controller, or cloud) | Fusion gateway |
|---|---|---|
| Style | OAuth2 `client_credentials` | Username/password web session |
| Token endpoint | `POST {api_url}/openapi/authorize/token` | `POST {api_url}/{omadacId}/api/v2/login` |
| Initial fetch | `grant_type=client_credentials` query param + JSON body `{omadacId, client_id, client_secret}` | JSON body `{username, password}` → `{result: {token}}` |
| Refresh | `grant_type=refresh_token` as **query params** (not body); on HTTP 401 or error codes `-44114/-44111/-44106`, falls back to full `client_credentials` re-auth | None — full re-login only |
| Request header | `Authorization: AccessToken={token}` (non-standard scheme, not `Bearer`) | `Csrf-Token: {token}` + `Omada-Request-Source: web-local` |
| omadacId discovery | Manual — user copies from controller UI (Settings → Platform Integration → Open API) | Automatic — unauthenticated `GET {api_url}/api/info` → `{result: {omadacId}}` |
| Sites | Multi-site | Single site only, auto-selected |
| Reactive re-auth on API call | HTTP 401 or error codes `-44112/-44113` (token expired/invalid) → refresh, retry once | Any auth failure → clear token, full re-login |

**Base URL / discovery:**
- Cloud: static per-region hardcoded map (US/EU/AP `*-omada-northbound.tplinkcloud.com`), not discovered.
- Local/Fusion: user types the base URL directly. No mDNS/auto-discovery.
- No general "getControllerInfo" endpoint exists in the documented Open API;
  `GET /api/info` is Fusion-specific and undocumented outside this reverse-engineered use.
- API version is fixed per-endpoint in the URL path (`/openapi/v1/...`,
  `/openapi/v2/...`); there is no runtime version negotiation.

**Site handling:**
- List: `GET /openapi/v1/{omadacId}/sites?pageSize=100&page=1` →
  `{result: {data: [{siteId, name, region, ...}], totalRows}}`.
- All subsequent per-site calls are shaped
  `/openapi/v{n}/{omadacId}/sites/{siteId}/...`.

**Token lifecycle:**
- Access token ~2h, refresh token ~14d (both authoritative from the API
  response's `expiresIn`, not hardcoded).
- Proactive refresh 5 minutes before expiry; reactive refresh on 401/token-error codes.
- No client-side rate limiting is implemented anywhere in the reference code.

**Known quirks worth carrying forward:**
- VLAN `mode == 0` (Default): `vlanId` and `vlanSetting.customConfig` must be
  **omitted from the payload entirely**, not nulled — naive read-modify-write
  will corrupt this.
- Single-port profile/PoE-mode write endpoints return `-1` on many firmware
  versions; the reference integration always uses the batch **multi-ports**
  endpoint instead.
- Clients v2 endpoint (`POST .../clients`) can return `-1600` on some
  (notably Fusion) firmware, meaning "endpoint not supported here" —
  triggers a permanent fallback to the v1 GET endpoint for that session.
- Write-access (viewer vs. editor) detection is done via a non-destructive
  read/write probe on the site LED setting; error codes `-1005`/`-1007` mean
  read-only credentials.
- TLS verification is disabled unconditionally for **all** controller types
  in the reference code, including cloud. This tool intentionally does
  **not** carry that over — see §5.7.
- No separate reusable Python API client package exists (aspirational only,
  per the project's own contributor docs) — this tool writes its own HTTP
  layer.

### 2.2 Vendored OpenAPI spec

`openapi/openapi.json` inside the HA integration repo documents ~1,644
paths. This tool depends on an equivalent spec (see §4.1) as its source of
truth for what resources exist and their JSON schemas. The `authorize/token`
and Fusion `api/info`/`api/v2/login` endpoints are **not** in this spec —
they're out-of-band and hand-coded per §2.1.

---

## 3. Functional Requirements

### FR-1 — Controller profile management
- The tool maintains a named list of controller connection profiles (not a
  fixed source/target pair), each with: name, type (`local` | `cloud` |
  `fusion`), base URL, and type-appropriate credentials (`omadacId` +
  client ID/secret for classic; username/password for Fusion, with
  `omadacId` auto-discovered).
- Each session, the operator picks which saved profile is the source and
  which is the target from independent selectors. A profile may be used as
  source in one session and target in another.
- Profiles persist in a local JSON file (see FR-8).

### FR-2 — Site selection
- After connecting to a controller profile, the tool lists its sites
  (`GET .../sites`) and lets the operator pick one site to read from
  (source) and one to write to (target) — independently, so source and
  target site names/IDs need not match.
- Fusion controllers: site list has exactly one entry and is auto-selected
  (per reference behavior), consistent with FR-2's "pick a site" model
  degenerating to a no-op choice.

### FR-3 — Reading settings
- Reads are **site-scoped only**. Controller/MSP-level (non-site) resources
  are out of scope for v1 (§6, deferred).
- The tool discovers the set of readable/writable resources dynamically
  from the OpenAPI spec (§4.1) rather than a hardcoded list — new resources
  in a spec update should appear without code changes to the resource list
  itself.
- Each resource type is read independently. A failure on one resource type
  (permission error, transient network error, etc.) is recorded against
  that resource type only and does not abort the rest of the read (FR-3.1).
- A resource type that the controller doesn't support at all (404, or a
  known "not supported" error code such as `-1600`) is recorded as
  **unsupported**, distinct from a read **error** (FR-3.2).

### FR-4 — Snapshots
- A completed read (all resource types for one site, with their
  success/error/unsupported status) is saved as a single JSON file to a
  local `snapshots/` directory.
- Filename pattern: `{site-name}_{controller-name}_{timestamp}.json`.
- The snapshot file contains: controller identity, site identity, capture
  timestamp, and the per-resource-type data plus its read status.
- A previously saved snapshot can be loaded and used in place of a live
  source read for diffing/writing. The target side of a diff is always a
  live read (you're about to write to it; a stale target view is unsafe).

### FR-5 — Diff view
- For each resource type, source and target objects are matched by natural
  key (first available of a schema-derived candidate field list — `name`,
  `ssid`, `profileName`, etc. — falling back to the object's own ID only if
  no name-like field exists).
- Each matched pair is classified as **identical**, **differs**, or
  unmatched (**source-only** / **target-only**).
- The UI provides a toggle to hide identical items, showing only diffs plus
  source-only/target-only items.
- Resource types marked unsupported on either side (FR-3.2) are shown in a
  separate, collapsed "Unsupported" section, excluded from the main diff
  counts.
- Resource types with a read error (FR-3.1) are flagged distinctly (e.g. a
  warning badge) rather than silently omitted.

### FR-6 — Selection
- Selection granularity is **whole-object**: one checkbox per object
  (matched pair, source-only item, or — if enabled — target-only item for
  deletion). Checking a differing object's box means "write all of this
  object's fields to target," not a per-field merge.
- Source-only items, when selected, are queued as **create** operations.
- Differing items, when selected, are queued as **update** operations.
- Target-only items are left untouched by default. An explicit, separately
  surfaced opt-in per item (or per resource type) allows queuing them as
  **delete** operations, with distinct (e.g. red/warning) UI treatment
  given the destructive/irreversible nature.

### FR-7 — Cross-reference (ID) remapping
- The tool maintains an in-memory source-ID → target-ID map, populated as
  matched objects are identified (existing target ID) and as new objects
  are created during a write run (newly assigned target ID).
- Before sending any write payload, fields matching a maintained
  reference-field lookup table (e.g. `networkId` → resource type
  `lan-networks`, `vlanId` → `lan-vlans`; see §4.3) are rewritten from the
  source ID to the mapped target ID.
- If a reference field's source ID has no target mapping yet (referenced
  object not selected, not yet created, or unmatched), the write is held
  back for a later retry pass (§FR-9) rather than sent with a stale/wrong ID.
- If a reference remains unresolved after the retry queue converges, it's
  reported as a real failure with the specific field/object named, so the
  operator can select the missing dependency or resolve it by hand.

### FR-8 — Write execution
1. **Plan**: from the current selection, build an ordered list of pending
   requests (method, path, resource type, object name, operation
   create/update/delete).
2. **Preview**: show the full plan to the operator before anything is sent.
   Nothing is written until the operator explicitly confirms ("Execute
   Plan").
3. **Execute**: run the plan as a retry-until-stable queue (FR-9).
4. **Report**: show per-item results — succeeded, failed (with API error
   detail), or unresolved-reference-blocked.

### FR-9 — Write ordering (retry-until-stable)
- No upfront dependency graph is built. All selected writes are attempted
  in a pass; anything that fails due to an unresolved reference (FR-7) or a
  dependency-shaped API error is re-queued for the next pass.
- Passes repeat until either the queue is empty or a pass makes no forward
  progress (nothing newly succeeds).
- Whatever remains unresolved after convergence is a genuine failure, not
  an ordering artifact, and is reported as such (FR-8.4).

### FR-10 — Credential storage
- Controller profiles (FR-1), including secrets (client secret, or Fusion
  password), are stored in a local JSON config file on disk, outside any
  git-tracked directory of the tool itself (or explicitly gitignored).
- File permissions restricted to the owner (mode 600) where the OS supports
  it.

### FR-11 — TLS handling
- TLS certificate verification is **on by default** for every controller
  connection.
- Each controller profile (FR-1) may set an explicit "self-signed
  certificate" flag to disable verification for that connection only. This
  is a deliberate deviation from the reference integration's unconditional
  `verify=False` (§2.1) — see §5.7 for rationale.

### FR-12 — Deployment / runtime
- The tool runs as a single local process (`python -m omada_migrator` or
  equivalent), serving both the API and the web UI, bound to `localhost`
  only. No authentication layer on the tool's own web UI — it is a
  single-user local utility, not a hosted service.

---

## 4. Technical Design Requirements

### TD-1 — Stack
- Backend: Python 3.11+, FastAPI, an async HTTP client (httpx or aiohttp)
  for controller calls, Pydantic for request/response models where typed
  models are used (auth, snapshot envelope) — not for the ~1600 generic
  resource schemas, which are handled generically (TD-4.1).
- Frontend: server-rendered or vanilla JS + `fetch` against the FastAPI
  JSON API. No frontend build step, no npm toolchain.

### TD-2 — Auth layer
- Mirrors §2.1 exactly: an `AuthStrategy` abstraction with two
  implementations (`ClientCredentialsAuth`, `WebSessionAuth`), matching
  token endpoints, headers, refresh/re-login behavior, and error-code
  handling (`-44112/-44113` reactive refresh; `-44114/-44111/-44106`
  refresh-fallback; `-1600` endpoint-unsupported fallback where applicable).
- `omadacId` resolution: manual entry for classic profiles; automatic via
  `GET {api_url}/api/info` for Fusion profiles.

### TD-3 — Resource discovery (schema-driven)

#### TD-3.1 — Registry construction
- At startup (or on demand, cached), parse the vendored OpenAPI spec.
- A "resource type" is a path template under
  `/openapi/v{n}/{omadacId}/sites/{siteId}/...` that has both:
  - a GET operation (list or get) returning objects, and
  - at least one write operation (POST create, and/or PATCH/PUT update,
    and/or DELETE) on a related path.
- Each registry entry records: path template(s) for read/create/update/delete,
  HTTP methods, JSON schema for the object, and whether it's a list or
  singleton resource.
- Paths without a matching write operation (pure read-only catalogs, e.g.
  DPI application catalog) are excluded from the registry — nothing to
  migrate.
- Non-site-scoped paths are excluded (§FR-3 site-scope restriction).

#### TD-3.2 — Quirks overrides
- A small hand-maintained overrides module holds per-resource-type
  exceptions to generic behavior, seeded from §2.1's known quirks:
  - VLAN `mode==0` field omission on write.
  - Batch multi-port endpoint preferred over single-port write endpoints.
  - Any other quirk discovered during implementation/testing.
- Overrides are looked up by resource type/path; anything without an
  override uses the generic schema-driven read/diff/write path.

#### TD-3.3 — Generic engine
- **Read**: generic GET (with pagination handling for list resources)
  driven purely by the registry entry, no per-resource-type code.
- **Diff**: generic structural JSON comparison between matched source/target
  objects, using the schema's field list to decide what to compare
  (excluding server-assigned fields like ID, timestamps, etc. — a fixed
  ignore-list of common such field names, extendable via the quirks
  override if a resource type has a non-standard one).
- **Write**: generic payload construction (whole object, per FR-6) with
  reference-field rewriting (FR-7) applied before send, quirks overrides
  applied if present for that resource type.

### TD-4 — Reference-field lookup table
- A small hand-maintained mapping, `{field_name: resource_type}` (e.g.
  `networkId → lan-networks`, `vlanId → lan-vlans`, `radiusProfileId →
  profiles/radius`), used by FR-7's ID remapping.
- Fields not present in the table are treated as opaque values (not
  remapped) — safer to leave an unmapped ID unresolved-and-flagged than to
  guess.
- Grown incrementally as resource types are exercised; not required to be
  complete at launch, since unresolved references are surfaced to the
  operator (FR-7) rather than silently mis-written.

### TD-5 — Snapshot file format
```json
{
  "controller": { "name": "...", "type": "local|cloud|fusion", "url": "..." },
  "site": { "site_id": "...", "name": "..." },
  "captured_at": "2026-07-17T14:30:00Z",
  "resources": {
    "<resource-type-key>": {
      "status": "ok | error | unsupported",
      "error": "...",
      "objects": [ ... ]
    }
  }
}
```

### TD-6 — Controller profile file format
```json
[
  {
    "name": "Old Office Controller",
    "type": "local",
    "url": "https://192.168.1.100:8043",
    "insecure_tls": true,
    "omadac_id": "...",
    "client_id": "...",
    "client_secret": "..."
  },
  {
    "name": "Home Fusion Gateway",
    "type": "fusion",
    "url": "https://192.168.1.1",
    "insecure_tls": true,
    "username": "...",
    "password": "..."
  }
]
```

---

## 5. Design Rationale (why, not just what)

Captured so a future change to any of these isn't made blind to the
tradeoff already considered.

**5.1 Schema-driven over hand-curated resources.** The operator explicitly
chose "everything configurable" as scope (~1600 paths). Hand-writing a typed
resource class per endpoint doesn't scale to that; parsing the spec and
driving read/diff/write generically does, at the cost of relying on schema
quality and needing the quirks-override escape hatch for the cases where
generic behavior is wrong.

**5.2 Natural-key matching over manual mapping UI.** IDs never match across
independently-provisioned controllers. Matching by name is what a human
would do eyeballing two controllers side by side, and needs no extra UI
step. A manual-mapping UI was considered and rejected as unnecessary
friction for the common case (same names on both sides) — it can be revisited
if renamed-object migrations turn out to be common in practice.

**5.3 Retry-until-stable over an explicit dependency graph.** Inferring a
type-level dependency graph from schemas (via `*Id` field heuristics) risks
missing or misordering references the heuristic doesn't catch, and silently
producing a wrong write order. Letting the API's own errors drive requeuing
means the ordering is always correct by construction — if it can succeed in
some order, this converges to that order; if it can't, that's a real error
either way.

**5.4 Whole-object over field-level selection.** Field-level selection
invites constructing payloads that mix source and target field values in
combinations the Omada API was never validated against (many objects likely
require internally-consistent field sets). Whole-object writes are what the
API actually expects, and the diff view still shows the field-by-field
breakdown for visibility — only the *unit of action* is coarser than the
unit of *display*.

**5.5 Hand-maintained reference-field table over pure heuristic inference.**
Silently guessing wrong on which resource type an ID field points to writes
a bad ID into a live network controller (potential outage). A small table
that starts incomplete and fails safe (flag for manual review) is strictly
safer than a heuristic that might be confidently wrong.

**5.6 Local-only, no tool-level auth.** This is a single-user, occasional-use
utility for a homelab-scale migration, run on the operator's own machine.
Binding to localhost with no auth layer matches the actual threat model and
avoids building unneeded login/session infrastructure — consistent with
YAGNI. Revisit only if the tool is later deployed somewhere multi-user or
network-reachable (out of scope today per FR-12).

**5.7 TLS verification on by default, deviating from the reference
integration.** The HA integration disables verification unconditionally,
including for cloud endpoints — reasonable for a device-local HA add-on
talking mostly to local hardware, but this tool is a general migration
utility that may also point at TP-Link Cloud, where silently accepting any
certificate is an unnecessary MITM exposure. Making it opt-in per profile
preserves the practical need (self-signed local/Fusion certs) without
weakening the cloud path by default.

**5.8 Snapshot-based decoupling of read and write.** Explicitly requested:
"store the read settings so you can also load settings and write them to
the target controller." This also happens to make the write flow safer in
practice — a loaded snapshot is a frozen, inspectable artifact, versus a
live source read that could itself change mid-migration.

---

## 6. Out of Scope (v1)

- Controller/MSP-level (non-site-scoped) settings — admin accounts, org
  structure, etc. (§FR-3).
- Field-level (as opposed to whole-object) selective writes (§5.4).
- Manual visual object-mapping UI for renamed objects (§5.2) — natural-key
  matching only.
- Any automated/scheduled migration — this is an interactive, one-session-
  at-a-time tool.
- Multi-user access, remote deployment, or auth on the tool's own UI (§5.6) —
  localhost-only.
- Device firmware, live client state, PoE live status, and other
  runtime/telemetry data — "settings" only, matching the migration use case.
- Automatic dependency-graph inference for write ordering (§5.3) — superseded
  by retry-until-stable.

---

## 7. Verification Plan

Each functional requirement has a corresponding check. "Manual" means
exercised against a real or test-double Omada controller during
development; "Unit" means an automated test with mocked HTTP responses.

| Req | Verification | Method |
|---|---|---|
| FR-1 | Add/edit/remove controller profiles of all three types; verify persisted file matches TD-6 shape; verify secrets are not logged. | Manual + unit (file round-trip) |
| FR-2 | Connect to a real (or recorded-fixture) classic controller with 2+ sites and a Fusion gateway; confirm site list and single-site auto-select for Fusion. | Manual |
| FR-3 | Point at a controller; confirm every registry resource type is attempted; force one endpoint to 404 and one to time out; confirm the read completes and both are flagged distinctly (unsupported vs. error), not fatal. | Manual + unit (mocked failures) |
| FR-4 | Perform a read, confirm snapshot file appears with correct name/shape; load it in a later session (after restarting the tool) and confirm it diffs identically to a fresh read of the same state. | Manual |
| FR-5 | With known source/target differences (rename, field change, add, remove), confirm classification (identical/differs/source-only/target-only) is correct; toggle "hide identical" and confirm only non-identical items remain visible. | Manual + unit (diff engine with fixture data) |
| FR-6 | Select a "differs" object; confirm the resulting write plan payload contains the full source object, not a partial merge. Select a target-only item for delete; confirm it requires the explicit opt-in UI action, not the default checkbox. | Manual |
| FR-7 | Construct a scenario: new LAN network + new SSID referencing it, both selected. Execute; confirm the SSID's `networkId` in the actual sent payload is the target's newly created ID, not the source's. Then construct an unresolvable case (referenced object not selected) and confirm it's reported as unresolved with the specific field named, not silently sent or silently dropped. | Unit (payload inspection with mocked write responses) |
| FR-8 | Confirm the preview screen lists every planned request accurately before any network call is made (assert zero write calls occur before "Execute" is clicked); confirm the post-execution report distinguishes succeeded/failed/unresolved per item. | Manual + unit |
| FR-9 | Construct a multi-level dependency chain (A required by B required by C) submitted in reverse/random selection order; confirm all three succeed via convergence. Construct a case with a genuinely broken reference (points at nothing, ever); confirm it's reported as a failure after convergence, not retried forever. | Unit |
| FR-10 | Inspect the config file on disk: correct permissions (600), plaintext secrets present only there, file excluded from any git tracking. | Manual |
| FR-11 | Add a profile without the self-signed flag against a self-signed endpoint; confirm the connection fails with a clear TLS error rather than silently succeeding. Add it with the flag set; confirm it connects. Confirm a cloud-type profile never exposes the flag. | Manual |
| FR-12 | Start the tool; confirm it's reachable at `localhost` and not from another machine on the LAN; confirm no login screen exists. | Manual |
| TD-3 | Update/replace the vendored OpenAPI spec fixture with an added endpoint; confirm the new resource type appears in the registry and is readable without code changes (beyond the spec file itself). | Unit |
| TD-3.2 | Exercise the VLAN `mode==0` write case; confirm the sent payload omits `vlanId`/`vlanSetting.customConfig` rather than nulling them. Exercise a port-profile write; confirm the batch multi-ports endpoint is used, not the single-port one. | Unit (payload inspection) |

### 7.1 Definition of done for v1

- All FR-1 through FR-12 verification rows pass.
- A full end-to-end manual run against two real (or faithfully recorded)
  Omada environments — at minimum one classic-to-classic migration and one
  classic-to-Fusion (or Fusion-to-classic) migration — completes: read
  source, review diff, select items including at least one create, one
  update, and one cross-referencing pair, preview, execute, and confirm the
  target controller reflects the intended state via a fresh read.
- No unhandled exception during a read or write run halts the tool process
  itself (individual resource/request failures are caught and reported per
  FR-3/FR-8, not fatal to the run).
