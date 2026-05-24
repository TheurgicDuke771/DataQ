# ADR 0001 — Trunk-based branching with squash-merge into `main`

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** @TheurgicDuke771

## Context

DataQ v1 has an 8-week timeline and a small team (starting solo, expected to grow). We need a branching model that supports frequent integration, keeps `main` history readable, and aligns with the working-agreement of one functionality per commit.

## Decision

Adopt **trunk-based development** with short-lived feature branches off `main`, and **squash-merge** as the only allowed merge method into `main`.

- All work happens on a branch named `feature/<desc>`, `fix/issue-<N>-<desc>`, `chore/<desc>`, or `docs/<desc>`.
- Branches are short-lived (ideally hours to a couple of days), opened as PRs against `main`.
- PRs are squash-merged — the PR title becomes the single commit on `main` (so PR titles MUST follow conventional commits — see ADR 0002).
- No long-lived `develop` branch.
- `main` is protected via a GitHub ruleset (deletion blocked, force-push blocked, linear history required, PR required, squash-only merge, dismiss stale reviews on new push). Admin (sole maintainer) can bypass in emergencies.
- Auto-delete head branches on merge.

## Consequences

**Positive**
- `main` history reads as one commit per delivered functionality — matches working-agreement #1.
- Short-lived branches force small, reviewable changes.
- No long-lived branches to keep in sync.
- Linear history makes `git bisect` reliable.

**Negative**
- Loses individual WIP commits inside a PR. Mitigated: those commits remain visible in the PR view on GitHub.
- Solo dev currently has `required_approving_review_count: 0` — must bump to 1 when a second contributor joins (tracked as a future change).

## Alternatives considered

- **GitFlow** — rejected. The `develop` / `release` / `hotfix` overhead is unjustified for an 8-week single-tenant v1; release cadence is continuous, not versioned.
- **Merge commits preserving branch history** — rejected. Squash gives one commit per functionality on `main`; preserved branch history would dilute that signal and clutter `git log`.
- **Rebase-and-merge** — rejected. Preserves all WIP commits on `main`, breaking the one-functionality-per-commit principle.
