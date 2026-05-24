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
- `--severity critical|high|medium|low` (defaults to `medium`; use `critical` only for production-down / data-loss; never use `critical` for security — see `--security` flag below)
- `--type bug|enhancement|documentation` (defaults to `bug`; maps to GitHub label)
- `--security` — flag for security-adjacent issues. If set, the skill SHOULD NOT proceed with `gh issue create`; instead it prints the GitHub Security Advisories URL and exits, per the security-disclosure policy (see issue [#9](https://github.com/TheurgicDuke771/DataQ/issues/9) for context).
- `--blocker-for-week <N>` — optional, marks the issue as a Week-N blocker.

## Steps

1. **If `--security` is set**, do not create a public issue. Print:
   ```
   Security-adjacent finding — do not file as a public issue.
   Report via GitHub Security Advisory:
   https://github.com/TheurgicDuke771/DataQ/security/advisories/new
   ```
   Exit successfully without calling `gh issue create`.

2. **Resolve the label** from `--type`:
   - `bug` → `bug`
   - `enhancement` → `enhancement`
   - `documentation` → `documentation`

3. **Build the issue title** as a plain descriptive sentence (not a `fix:` / `feat:` prefix — that belongs on the fixing PR). Examples:
   - "ADR template missing Consulted field"
   - "Architecture diagram doesn't include Apache Airflow"

4. **Build the issue body** using the template below.

5. **Call `gh issue create`.** The body template contains backticks, code blocks, and `$`-signs that break shell-interpolated `--body "..."` quoting. Use `--body-file` with a temp file instead:
   ```bash
   tmp=$(mktemp); printf '%s' "$BODY" > "$tmp"
   gh issue create --title "$TITLE" --label "$LABEL" --body-file "$tmp"
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
- **Issue title is a plain descriptive sentence.** No `fix:` / `feat:` prefix on issues — those are PR-side conventional types per ADR 0002. Issue templates at `.github/ISSUE_TEMPLATE/bug.md` currently pre-fill the wrong prefix; this skill counteracts that until issue [#8](https://github.com/TheurgicDuke771/DataQ/issues/8) is resolved.
- **Backlink is the source of truth.** Always include the /review comment URL or source PR number so the issue can be cross-referenced.
- **Fix PR must reference `Fixes #N`** to auto-close on merge (working-agreement #3 + PR template's "Linked issue" section).
- **Do not create the fix branch from this skill.** Branch creation is a separate workflow.

## Anti-patterns to avoid

- Don't create an issue for findings on an *unmerged* PR — fix those in-place with a fixup commit on the branch. Issues are for findings deferred to a later PR or found after merge (see [/review-before-merge memory rule](https://github.com/TheurgicDuke771/DataQ/blob/main/CLAUDE.md)).
- Don't bulk-create issues for trivial nits. Use this for items that genuinely need tracking — typos and one-line clarity fixes can be done inline.
- Don't include sensitive details (customer names, secrets, internal URLs, exploit payloads) in a public issue. If those are involved, the finding belongs in a Security Advisory.
