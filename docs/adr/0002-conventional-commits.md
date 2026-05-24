# ADR 0002 — Conventional commits for PR titles and commit messages

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** @TheurgicDuke771

## Context

With squash-merge as the only allowed merge method (ADR 0001), each PR becomes exactly one commit on `main` whose message is the PR title. The PR title format therefore drives the readability of `git log` and enables future automation (changelog generation, release notes).

## Decision

Use **Conventional Commits** for all PR titles and individual commits.

Allowed type prefixes:

| Type | Use for |
|---|---|
| `feat:` | New user-facing functionality |
| `fix:` | Bug fix (PR body MUST include `Fixes #N` per working-agreement #3) |
| `chore:` | Tooling, dependencies, repo housekeeping |
| `docs:` | Documentation-only change (CLAUDE.md, ADRs, README, code comments) |
| `test:` | Test-only change |
| `refactor:` | Internal change with no behaviour change |

Format: `<type>(<optional-scope>): <imperative summary>`

Examples:
- `feat(orchestration): add Airflow callback webhook handler`
- `fix(adf): debounce duplicate trigger events within 30s window`
- `chore: pin GX Core to 1.x`
- `docs: add ADR 0005 for severity tier weights`

## Consequences

**Positive**
- `git log --oneline` reads as a categorised changelog.
- Enables future tooling: auto-changelog, release-please, semantic versioning.
- PR titles are self-documenting.

**Negative**
- One more thing for new contributors to learn. Mitigated: the PR template includes a "Type of change" checklist that nudges the right prefix.

## Enforcement

- PR template includes the convention.
- Future enhancement (post-W1): a GitHub Action that fails CI if the PR title does not match the convention.
