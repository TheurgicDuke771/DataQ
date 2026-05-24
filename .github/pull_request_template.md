<!--
Per working-agreements #1, #3, #5, #11, #25:
- One functionality per PR (squash-merges into one commit on main)
- Reference any related GitHub issue (Fixes #N for defect fixes)
- Manually tested before merge (until automated tests land in Week 8)
-->

## Summary

<!-- 1–3 bullets: what this PR does and why. Focus on the "why". -->
-
-

## Linked issue

<!-- For defect fixes, use "Fixes #N" so the issue auto-closes on merge.
     For feature work, use "Refs #N" or leave blank. -->
Fixes #

## Type of change

<!-- Tick all that apply. -->
- [ ] feat — new functionality
- [ ] fix — bug fix (linked issue above)
- [ ] chore — repo housekeeping / tooling
- [ ] docs — documentation only
- [ ] refactor — no behaviour change
- [ ] test — test-only change

## Checklist

- [ ] **Manually tested locally** (required pre-Week-8; describe what you tested below)
- [ ] **Single functionality** — no unrelated changes piggybacked
- [ ] **Conventional commit title** (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`)
- [ ] **No secrets, credentials, or `.env` files committed**
- [ ] **Black + Ruff + mypy pass locally** (for Python changes)
- [ ] **Prettier + ESLint pass locally** (for frontend changes)
- [ ] **Tests added/updated** (required from Week 8 onward)
- [ ] **Docs / ADRs updated** if user-facing or architectural change
- [ ] **Security implications considered** (auth, input validation, secret handling, PII in logs)

## Schema migration?

- [ ] No schema change
- [ ] Yes — Alembic migration included, **tested up and down locally**, rollback plan in description below

## Manual test notes

<!-- What did you run / click to verify this works? -->

## Security / risk notes

<!-- Anything reviewers should pay extra attention to. Public endpoints? New secrets? PII handling? -->
