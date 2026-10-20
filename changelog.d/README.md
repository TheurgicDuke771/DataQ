# changelog.d — one file per change

Every PR that changes something a user, deployer or API client would notice adds **one
small file here** instead of editing `docs/site/reference/changelog.md`. The published
page renders these under **Unreleased** at build time; at release
`scripts/changelog.py assemble` moves them into a dated section and deletes them.

## Filename

```
<number>.<section>.md         e.g. 2395.changed.md
<number>-<n>.<section>.md     a second entry for the same number, e.g. 2440-2.added.md
```

- `<number>` — the issue the change belongs to (the PR number if there is no issue).
- `<n>` — `2`, `3`, … for further fragments of the same number.
- `<section>` — `breaking` | `security` | `added` | `changed` | `fixed`.

Only files matching this grammar (plus this README) may live here.

## Body

The bullet's text in Markdown, **without** the leading `- `, continuation lines unindented.
Lead with a bold phrase, as the existing entries do, and describe the behaviour — never cite
an issue or PR number, the page is public (CONTRIBUTING rule 41).

```markdown
**Changelog entries are collected one file per change.** A PR adds a fragment under
`changelog.d/` and the published page assembles them, so two PRs never conflict on the
changelog.
```

## Commands

```bash
python scripts/changelog.py lint                 # every fragment is well-formed (pre-commit + CI)
python scripts/changelog.py render-unreleased    # preview the Unreleased section
python scripts/changelog.py check --base origin/main   # what CI asks of a PR
python scripts/changelog.py assemble --version 1.3.0 --date 2026-11-29   # at release
```

A PR that touches user-facing paths and needs no entry carries the `no-changelog` label.
Dependabot PRs are exempt.
