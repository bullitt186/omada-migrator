# AGENTS.md

<!-- repo-kit:begin v2 (managed, do not edit; https://github.com/bullitt186/repo-kit) -->
## How to work in this repository (all coding agents)

This block is shared by all repositories of this maintainer and kept in sync by
[repo-kit](https://github.com/bullitt186/repo-kit). Repository-specific
instructions follow below it. They add detail; where they contradict this block,
this block wins, unless an ADR in `docs/adr/` records the exception.

A pull request that follows this block passes review without rework. The
maintainer reviews against the same rules: the **Definition of Done** in
`CONTRIBUTING.md`.

### Build the right thing

1. Read this file to the end, `CONTRIBUTING.md`, `docs/SCOPE.md` and the
   architecture decision records in `docs/adr/` (start with `docs/adr/README.md`).
   An *Accepted* ADR and the scope are binding. If your change needs a different
   decision, write a new ADR that supersedes the old one, in the same pull request.
2. Work from an issue. A feature or behaviour change needs an issue the maintainer
   has labelled `accepted`; if there is none, open one, describe the problem (not
   only the solution) and wait. Only a **small fix** may skip the issue: it repairs
   documented behaviour, changes fewer than about 50 lines of code, and adds no
   dependency, interface, setting or file format.
3. Build what the issue asks for and nothing else. Ideas you have on the way become
   a new issue, not part of this pull request. No reformatting, renaming or
   refactoring that the change does not need.
4. Prefer the smallest complete change: reuse what the repository already has
   (helpers, patterns, dependencies) before adding anything; no new dependency for
   a few lines. Fix the root cause, not the symptom.
5. Never guess protocol values, API fields or hardware behaviour. Take them from a
   source and name it; mark anything unconfirmed as an assumption.

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
- reverses or replaces an earlier ADR, or changes `docs/SCOPE.md`.

Bug fixes and refactorings inside existing decisions need no ADR.

### Tests

Design the test cases from the specification, then implement the change together
with them. A one-test-at-a-time red/green cycle is not required.

- A bug fix starts with a regression test that reproduces the bug. Run it and
  confirm it fails **for the reason the bug describes**, not because of a build,
  import or signature error. Then fix the bug and commit the test with the fix.
- Expected values come from the specification: documentation, a protocol table,
  a manual, the issue, or a recorded real payload. Never from running the code
  under test and copying its output; such a test only proves the code does what
  it does.
- Assert observable results (output, state, sent request, raised error), not only
  that something was called.
- Tests are fast, deterministic and offline. A flaky test is a bug to fix, not to
  re-run.
- Changing an expected value in an existing test needs a reason in the commit
  message that names the source of the new value.
- Coverage shows what ran, not whether a test notices a wrong result. Run mutation
  testing on the code you changed (see Commands below); kill a surviving mutant
  with a test, or say in the pull request why it is equivalent.
- Do not weaken lint rules, thresholds or checks to make a change pass; fix the
  code. A justified exception gets a comment next to it and a note in the commit
  message. Thresholds are only ever raised.

### Documentation

Documentation changes in the same pull request as the behaviour it describes:
README, `docs/`, configuration examples, the ADRs and `CHANGELOG.md`. Outdated
documentation counts as a bug. Relative links in Markdown must point to files
that exist.

Changelog entries go under `[Unreleased]`, in the Keep a Changelog sections
(Added, Changed, Fixed, Removed, Security). Write them for the user of the
project: what changes for them and what they have to do, not which functions
moved. A breaking change gets an entry under "Upgrade notes" with the steps.
Name outside contributors and link their issue or pull request. Releases, tags
and version numbers are the maintainer's
([release standard](https://github.com/bullitt186/repo-kit/blob/main/docs/RELEASING.md)).

### Commits and branches

- One branch per topic from the current default branch; never commit to the
  default branch. Agents working in parallel use their own git worktree; never
  run checkout, switch, reset, stash or clean in a checkout someone else uses.
- Commit each completed step with a Conventional Commits message (`feat:`,
  `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`); the body says why.
  Code, comments, commit messages and pull requests are in English.
- Reference the issue: `Closes #N` on GitHub; `Refs #N` on Forgejo, which closes
  an issue as soon as a commit with `Closes` is pushed.
- Never commit or push on red; never use `--no-verify` or skip hooks; treat every
  hook finding as work to do.

### Before you open the pull request

- Run the repository's **fast check** before every commit and the **full check**
  when your change can affect what it covers (see Commands below). Both must pass.
- Merge or rebase onto the current default branch and run the checks again.
- Fill in the pull request template completely; tick only what you did and say
  why anything stays unticked.
- After pushing, watch CI yourself. A red check on your pull request is your work,
  also when it looks unrelated.
- No secrets, tokens, personal data, local paths, host names or IP addresses in
  code, tests, fixtures, logs, commits or pull request text.

### Continuous integration

CI follows the repo-kit [CI standard](https://github.com/bullitt186/repo-kit/blob/main/docs/CI_STANDARD.md):
tests, lint and format, type check, build, secret scan, dependency review, static
analysis and `PR hygiene`. PR hygiene fails when code changes come without a test
change or a `CHANGELOG.md` entry, when `docs/SCOPE.md` changes without an ADR,
when an ADR is malformed or missing from the index, when a changed Markdown file
has a broken relative link, when code from an outside contributor links no issue,
or when a pull request changes more than about 800 lines of code and tests. Only
the maintainer can waive a rule (labels `no-test-change`, `skip-changelog`,
`large-change`). Do not ask for a waiver when a test, entry or split is possible.

### Respect

Do not change CI configuration, branch protection, release workflows, licence,
`docs/SCOPE.md` or this block unless the issue asks for it. Maintainer decisions
recorded in issues and ADRs are not up for silent change.
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
| Install / set up | Python 3.11+; `pip install -e '.[dev]'`, or use [uv](https://docs.astral.sh/uv/) as below |
| Fast check (before every commit, also the stop hook) | `uv run --no-project --with-editable '.[dev]' pytest -q` (or `pytest -q` in an environment with the `dev` extra) |
| Full check (before every pull request) | same as the fast check |
| Mutation testing of changed code | not set up (#4) |
| Run the tool | `python -m omada_migrator` (serves on `127.0.0.1:8888`) |

There is no CI for the tests yet (#1), and no linter, formatter or type checker (#4); run the
fast check locally before every commit.

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
