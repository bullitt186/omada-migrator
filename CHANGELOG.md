# Changelog

All notable changes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/).

## [Unreleased]

### Contributors

- CI runs the tests (Python 3.11 and 3.14), Ruff lint and format, mypy and the package build on
  every pull request and on `main`; all dependencies are pinned in `uv.lock`, and a weekly mutation
  run (mutmut) reports how well the tests catch changes. The code was reformatted by Ruff; behaviour
  is unchanged (API errors now keep the original exception as their cause in logs).
- One setup for all coding agents and people ([repo-kit](https://github.com/bullitt186/repo-kit)):
  `AGENTS.md` is the single instruction file (`CLAUDE.md` imports it), `CONTRIBUTING.md` holds
  the Definition of Done that every pull request is reviewed against, a pull request template,
  issue forms, and architecture decision records in `docs/adr/` (ADR-0002 schema-driven
  resources, ADR-0003 retry-until-stable writes, ADR-0004 TLS verification), and `docs/SCOPE.md` with purpose and non-goals. The new
  `PR hygiene` check fails when code changes come without a test or changelog change, or when
  an ADR or a Markdown link is broken.
- repo-kit v2: `AGENTS.md` names a fast check (run before every commit and by the Claude Code
  stop hook in `.claude/settings.json`), the full check and the state of mutation testing;
  `CONTRIBUTING.md` adds how an idea becomes an accepted issue and the labels `accepted` and
  `out-of-scope`. New `Security` workflow (secret scan, dependency review) and weekly Dependabot
  updates for pip and GitHub Actions.
