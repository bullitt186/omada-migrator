# Architecture decision records

Decisions that shape this repository, one file each: `NNNN-kebab-case-title.md`,
numbered in order, written from `template.md`. When to write one: `AGENTS.md`.

- An *Accepted* ADR is binding. To change it, add a new ADR and set the old
  one's status to `Superseded by ADR-NNNN`; never rewrite an accepted decision.
- Agents and people write ADRs the same way, in the pull request that implements
  the decision. Merging the pull request accepts the ADR.
- Every ADR is listed in the table below (PR hygiene checks this).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-resources-discovered-from-the-openapi-spec.md) | Resources are discovered from the OpenAPI spec | Accepted |
| [0003](0003-write-order-by-retry-until-stable.md) | Write order by retry-until-stable, not a dependency graph | Accepted |
| [0004](0004-tls-verification-on-by-default.md) | TLS verification on by default | Accepted |
