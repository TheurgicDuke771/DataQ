---
name: gh-issue-from-finding
description: Create a properly-formatted GitHub issue from a code-review or runtime finding, with backlink to the source PR/comment and the project's required fields populated. Use when a defect or non-blocking polish item is identified during /review or normal work, and working-agreement #3 ("defects → GitHub issue, never silent fixes") applies.
disable-model-invocation: true
---

# gh-issue-from-finding

## Purpose

Operationalize working-agreement #3: every defect (and every deferred-but-known polish item) becomes a GitHub issue *before* it gets fixed. This skill compresses the manual ceremony of writing a well-formatted issue into one command.

## Usage

User invokes with a short description of the finding. Required context the skill must collect:
- `--from-pr <N>` — source PR where the finding was identified (optional but strongly preferred for traceability)
- `--from-comment <url>` — direct link to the /review comment (optional)
- `--severity critical|high|medium|low` (defaults to `medium`; use `critical` only for production-down / data-loss; never use `critical` for security — see `--security` flag below). Maps to a `priority/*` label **and** a `**Severity:**` line in the body (see Steps 2–4).
- `--type bug|enhancement|documentation` (defaults to `bug`; maps to GitHub label)
- `--security` — flag for security-adjacent issues. If set, the skill SHOULD NOT proceed with `gh issue create`; instead it prints the GitHub Security Advisories URL and exits, per the security-disclosure policy (see issue [#9](https://github.com/TheurgicDuke771/DataQ/issues/9) for context).
- `--milestone <title>` — optional override. Every issue gets a milestone (project convention). Default: the current feature-week milestone from CLAUDE.md §13 (e.g. `v1.1 Week 1 — Snowflake close-out + PATs`); use `v1.1 Backlog` for no-target-week items.
- `--blocker-for-week <N>` — optional, marks the issue as a Week-N blocker.

## Steps

1. **If `--security` is set**, do not create a public issue. Print:
   ```
   Security-adjacent finding — do not file as a public issue.
   Report via GitHub Security Advisory:
   https://github.com/TheurgicDuke771/DataQ/security/advisories/new
   ```
   Exit successfully without calling `gh issue create`.

2. **Resolve and validate the labels.**
   - **Type label** from `--type`: `bug` → `bug`, `enhancement` → `enhancement`, `documentation` → `documentation`.
   - **Priority label** from `--severity`: `critical` → `priority/P0`, `high` → `priority/P1`, `medium` → `priority/P2`, `low` → `priority/P3`.
   - **Validate both labels exist before filing.** Run `gh label list --json name -q '.[].name'` and confirm each resolved label is present. If a label is missing, stop and tell the user (e.g. `gh issue create` with an unknown `--label` fails with "label not found") — do not invent or create labels. The current repo labels include: `bug`, `enhancement`, `documentation`, `refactor`, `test`, `ci`, `security`, `epic`, `dependencies`, `priority/P0`–`priority/P3` (plus GitHub defaults and `week-N-carryover`). A `--type` outside `bug|enhancement|documentation` (e.g. `refactor`/`test`/`ci`) is allowed only if that label already exists.
   - Pass both to `gh issue create` as a comma-joined `--label "$TYPE_LABEL,$PRIORITY_LABEL"`.

2a. **Resolve and validate the milestone.** Every issue gets a milestone. Resolve against the live list: `gh api repos/TheurgicDuke771/DataQ/milestones --jq '.[] | .title + " | " + .state'`. Default = the **open** milestone whose title starts with `Week <N> — ` for the week CLAUDE.md §13 names — match against the live titles, do NOT transcribe §13's decorated headline (it reads "Week 7 of 8 — … — IN PROGRESS", which is not the milestone title). Post-v1 / no-target-week items → the open milestone matching "Backlog (post-v1". If the user names an already-closed milestone (rare backfill): create the issue without `--milestone`, then attach it via the REST procedure in Rules. If nothing matches at all, stop and tell the user — do not create milestones and do not leave the issue milestone-less. Otherwise pass `--milestone "$MILESTONE"`.

3. **Build the issue title** with the conventional-commit-style prefix matching `--type` (per working-agreement #3: `gh issue create --title "fix: <desc>"`) — `bug` → `fix:`, `enhancement` → `feat:`, `documentation` → `docs:`; an optional scope is fine. Examples of the shape used in practice:
   - "fix(db): DELETE /suites/{id} 500s once the suite has run"
   - "docs: architecture diagram doesn't include Apache Airflow"

4. **Build the issue body** using the template below.

5. **Call `gh issue create`.** The body template contains backticks, code blocks, and `$`-signs that break shell-interpolated `--body "..."` quoting. Use `--body-file` with a temp file instead:
   ```bash
   tmp=$(mktemp); printf '%s' "$BODY" > "$tmp"
   gh issue create --title "$TITLE" --label "$TYPE_LABEL,$PRIORITY_LABEL" --milestone "$MILESTONE" --body-file "$tmp"
   rm "$tmp"
   ```
   Capture the returned URL.

6. **Print to the user:**
   ```
   Filed: <issue url>
   Backlink it in the source PR with:
     gh pr comment <N> --body "Tracked as <issue url>"
   When the fix PR lands, include `Fixes #<issue number>` in its body.
   ```

## Body template

```markdown
<one-line problem statement>

**Severity:** <critical|high|medium|low> (priority/P0–P3)

## Items

- [ ] <actionable item 1>
- [ ] <actionable item 2>

## Why this is a follow-up, not a same-PR fix
<one sentence — typically "non-load-bearing for current PR" or "must land before Week N">

## Source
`/review` comment: <comment URL if provided>
Source PR: #<N> (if provided)
```

If `--blocker-for-week N` is set, prepend a bold line to the body:

> **Must land before Week N (<short reason>).**

## Rules

- **Never create a public issue when `--security` is set.** Always route to Security Advisories.
- **Issue title carries the conventional prefix** (`fix:` / `feat:` / `docs:`, optional scope) per working-agreement #3 and the issue templates. (An earlier proposal to switch issues to plain-sentence titles — [#8](https://github.com/TheurgicDuke771/DataQ/issues/8) — was closed NOT_PLANNED; prefix-style is the settled convention.)
- **Every issue gets a milestone** (in addition to labels). Feature-week milestone by default; `v1.1 Backlog` for unscheduled items. Note: assigning to an already-closed milestone can't be done via `gh issue edit --milestone <title>` — use the REST API with the milestone *number* (`gh api -X PATCH repos/TheurgicDuke771/DataQ/issues/<N> -F milestone=<num>`).
- **Backlink is the source of truth.** Always include the /review comment URL or source PR number so the issue can be cross-referenced.
- **Fix PR must reference `Fixes #N`** to auto-close on merge (working-agreement #3 + PR template's "Linked issue" section).
- **Do not create the fix branch from this skill.** Branch creation is a separate workflow.

## Test scenarios

Worked examples so the skill behaves consistently:

1. **Standard bug from review.** `gh-issue-from-finding "double-trigger race in _trigger_suites" --from-pr 215 --severity high --type bug` →
   validate `bug` + `priority/P1` exist; title `fix: double-trigger race in _trigger_suites`; milestone defaults to the current feature week; body carries `**Severity:** high`, the source-PR backlink, and `--label "bug,priority/P1"`.
2. **Default severity.** `... --type enhancement` with no `--severity` → defaults to `medium` → `priority/P2`; body `**Severity:** medium`; title prefix `feat:`.
3. **Security finding.** `... --security` → does **not** call `gh issue create`; prints the Security Advisory URL and exits 0.
4. **Unknown type label.** `... --type custom` → label validation fails (`custom` not in `gh label list`); stop and report instead of filing.
5. **Critical / production-down.** `... --severity critical --type bug` → `priority/P0`; body `**Severity:** critical`.

## Anti-patterns to avoid

- Don't create an issue for findings on an *unmerged* PR — fix those in-place with a fixup commit on the branch. Issues are for findings deferred to a later PR or found after merge (see [/review-before-merge memory rule](https://github.com/TheurgicDuke771/DataQ/blob/main/CLAUDE.md)).
- Don't bulk-create issues for trivial nits. Use this for items that genuinely need tracking — typos and one-line clarity fixes can be done inline.
- Don't include sensitive details (customer names, secrets, internal URLs, exploit payloads) in a public issue. If those are involved, the finding belongs in a Security Advisory.
