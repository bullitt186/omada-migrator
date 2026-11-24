# AGENTS.md

<!-- repo-kit:begin v1 (managed, do not edit; https://github.com/bullitt186/repo-kit) -->
## How to work in this repository (all coding agents)

This block is shared by all repositories of this maintainer and kept in sync by
[repo-kit](https://github.com/bullitt186/repo-kit). Repository-specific
instructions follow below it and win where they are more specific.

A pull request that follows this block passes review without rework. The
maintainer reviews against the same rules: the **Definition of Done** in
`CONTRIBUTING.md`.

### Before you change anything

1. Read this file to the end, `CONTRIBUTING.md`, and the architecture decision
   records in `docs/adr/` (start with `docs/adr/README.md`). An *Accepted* ADR is
   binding: do not work against it. If your change needs a different decision,
   write a new ADR that supersedes it, in the same pull request.
2. Find the issue for the work. If there is none and the change is more than a
   small fix, open one first and describe the problem, not only the solution.
3. Keep one topic per pull request. Do not reformat, rename or refactor code
   that your change does not need.

### Design first

Write the design down before the code: the behaviour that changes, the modules
and interfaces involved, and how it is tested. Put it in the issue or the pull
request description; for a small change the commit message is enough.

Write an ADR (`docs/adr/`, see its README) when the change:

- adds, removes or replaces a runtime dependency or external service,
- changes a public interface: API, CLI, configuration keys, file or data formats,
  entity or topic names, anything users or other systems rely on,
- changes the architecture: module boundaries, data flow, persistence, threading,
- sets a security, privacy or compatibility rule,
- reverses or replaces an earlier ADR.

Bug fixes and refactorings inside existing decisions need no ADR.

### Tests

- A bug fix starts with a regression test that fails for the reason the bug
  describes. Commit it with the fix.
- New behaviour comes with tests. Expected values come from the specification,
  documentation, a protocol table or a recorded real payload, never from running
  the code under test and copying its output.
- Assert observable results (output, state, sent request, raised error), not only
  that something was called.
- Changing an expected value in an existing test needs a reason in the commit
  message that names the source of the new value.
- Do not weaken lint rules, thresholds or checks to make a change pass; fix the
  code. A justified exception gets a comment and a note in the commit message.

### Documentation

Documentation changes in the same pull request as the behaviour it describes:
README, `docs/`, configuration examples, `CHANGELOG.md` (section `[Unreleased]`),
and the ADRs. Outdated documentation counts as a bug. Relative links in Markdown
must point to files that exist.

### Before you open the pull request

- Run the repository's full check (see below) and make it pass. Never commit or
  push on red, never use `--no-verify`.
- Rebase on or merge the current default branch and run the check again.
- Fill in the pull request template completely; tick only what you did.
- No secrets, tokens, personal data, local paths or host names in code, tests,
  logs, commits or pull request text.

### Continuous integration

CI runs the repository's checks and `PR hygiene` from repo-kit. PR hygiene fails
when code changes come without a test change or without a `CHANGELOG.md` entry,
when an ADR is malformed or missing from the index, or when a changed Markdown
file has a broken relative link. Only the maintainer can waive a rule, with the
labels `no-test-change` or `skip-changelog`. Do not ask for a waiver when a test
or changelog entry is possible.

### Respect

Do not change CI configuration, branch protection, release workflows, licence,
or this block unless the issue asks for it. Maintainer decisions recorded in
issues and ADRs are not up for silent change.
<!-- repo-kit:end -->

## This repository

### Purpose

Scope and non-goals: [docs/SCOPE.md](docs/SCOPE.md)

omada-migrator is a local, single-user web tool that migrates site-scoped
configuration from one TP-Link Omada controller (local, cloud or Fusion gateway)
to another through the Omada OpenAPI: read the source, diff it against the
target, select objects, preview the write plan, execute it. `REQUIREMENTS.md` is
the source of truth for what to build (functional requirements FR-*, technical
design TD-*, rationale in section 5, verification plan in section 7).

### Layout

| Path | Content |
|---|---|
| `src/omada_migrator/app.py` | FastAPI app: JSON API and the web UI, entry `python -m omada_migrator` |
| `src/omada_migrator/auth.py`, `api_client.py` | controller authentication strategies (TD-2) and HTTP client |
| `src/omada_migrator/registry.py`, `schema_meta.py`, `spec_store.py` | resource registry from the OpenAPI spec (TD-3.1), spec versions |
| `src/omada_migrator/quirks.py` | hand-maintained per-resource overrides (TD-3.2) |
| `src/omada_migrator/diff.py`, `write_engine.py`, `snapshot.py` | diff (FR-5), write plan and retry queue (FR-7 to FR-9), snapshots (FR-4) |
| `src/omada_migrator/config.py` | controller profile storage (FR-1, FR-10) |
| `src/omada_migrator/openapi_spec.json` | bundled Omada OpenAPI spec (vendored data) |
| `src/omada_migrator/static/index.html` | the web UI: vanilla JS, no build step |
| `tests/` | pytest tests; `tests/fixtures/` holds a reduced spec |

### Commands

| Task | Command |
|---|---|
| Install / set up | Python 3.11+; `pip install -e '.[dev]'` (or `uv run --extra dev …`) |
| Full check (run before every pull request) | `pytest -q` (or `uv run --extra dev pytest -q`) |
| Tests only | `pytest -q` |
| Run the tool | `python -m omada_migrator` (serves on `127.0.0.1:8888`) |

There is no CI for the tests yet (#1); run them locally before every pull request.

### Rules specific to this repository

- Follow `REQUIREMENTS.md`. Behaviour it does not cover is an implementation
  detail: prefer the standard library and existing code, no speculative
  abstractions. A change to a requirement or to a rationale in its section 5
  needs an ADR.
- Resources are discovered from the OpenAPI spec, not listed by hand
  (ADR-0002). Exceptions go into `quirks.py`; reference fields that need ID
  remapping go into `REFERENCE_FIELDS` in `write_engine.py`, never guessed (TD-4).
- Writes run as a retry-until-stable queue, no dependency graph (ADR-0003).
  Nothing is written before the operator confirms the previewed plan.
- TLS verification stays on by default; only a per-profile self-signed flag
  disables it (ADR-0004).
- The tool binds to `localhost` only and has no own login (FR-12). Profiles
  with secrets live in `~/.config/omada-migrator/config.json` with mode 600;
  never commit `config.json` or `snapshots/`, and never log secrets.
- Tests mock the controller HTTP API; they never talk to a real controller.
