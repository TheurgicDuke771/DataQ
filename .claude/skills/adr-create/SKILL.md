---
name: adr-create
description: Scaffold a new Architecture Decision Record under docs/adr/ with auto-numbered filename, standard frontmatter (Status / Date / Deciders / optional Consulted / Supersedes / Superseded by), and the canonical sections (Context, Decision, Consequences, Alternatives considered, Related). Use when the user wants to capture a significant architectural decision, or asks "let's ADR that."
disable-model-invocation: true
---

# adr-create

## Purpose

Create a new ADR in `docs/adr/` that follows the conventions documented in `docs/adr/README.md` and is consistent with ADRs 0001–0004 already in the repo.

## Usage

User invokes this skill with a short topic for the ADR. Optional args:
- `--status accepted|proposed|superseded` (default: `Accepted`)
- `--consulted "@user1, @user2"` (omit if engineering-only decision)
- `--supersedes 0007` (omit if not superseding)

## Steps

1. **Determine the next ADR number.** Read existing files in `docs/adr/` matching `NNNN-*.md`, find the highest number, increment by 1, zero-pad to 4 digits. If `docs/adr/` doesn't exist, start at `0001`.

2. **Derive a kebab-case slug** from the user's topic. Drop articles. Examples:
   - "Severity tier weights" → `severity-tier-weights`
   - "Use Pydantic Settings for config" → `use-pydantic-settings-for-config`

3. **Create the file** at `docs/adr/NNNN-<slug>.md` using the template below. Today's date in ISO format (`YYYY-MM-DD`). Deciders defaults to the git user (`git config user.name` mapped to GitHub handle if known, else literal name).

4. **Update the index** at `docs/adr/README.md`:
   - Add a row to the "Index" table at the end (above any "Pending" section).
   - If the new ADR was in the Pending list, remove it from there.

5. **Stage but do not commit.** The user runs `git add` + the commit themselves per working-agreement #1 (one functionality per commit, manual commit step).

6. **Print next steps:**
   - "Open `docs/adr/NNNN-<slug>.md` and fill in Context / Decision / Consequences / Alternatives."
   - "Branch suggestion: `docs/adr-NNNN-<slug>`"
   - "PR title suggestion: `docs: add ADR NNNN — <human title>`"

## Template

```markdown
# ADR NNNN — <Human-readable title>

- **Status:** Accepted
- **Date:** YYYY-MM-DD
- **Deciders:** @<github-handle>
- **Consulted:** <optional, e.g. @product-owner, @security-team>
- **Supersedes:** <optional, e.g. ADR-0007>
- **Superseded by:** <optional, e.g. ADR-0042>

## Context

<What problem are we deciding about? What forces are at play (constraints, requirements, prior decisions)? Keep to 1–3 paragraphs.>

## Decision

**<One-sentence statement of the decision, in bold.>**

<Then the details: how it works, what's in scope, what's out of scope.>

## Consequences

**Positive**
- <bullet>
- <bullet>

**Negative**
- <bullet>
- <bullet>

## Alternatives considered

- **<Alternative 1>** — rejected. <Why.>
- **<Alternative 2>** — rejected. <Why.>

## Related

- <Link to related ADRs, issues, PRs, or code paths.>
```

## Rules

- **Status field is required.** Default to `Accepted` unless `--status` says otherwise.
- **Date is today's date** in ISO format.
- **Consulted, Supersedes, Superseded by are optional** — include the label even if empty so future editors see the slot.
- **Body sections in this exact order:** Context → Decision → Consequences (with Positive/Negative subheadings) → Alternatives considered → Related.
- **Slug must match the convention** in `docs/adr/README.md`: lowercase, kebab-case, no leading article.
- **Filename matches title:** `NNNN-<slug>.md` where the slug is also derivable from the H1 title.
- **README index is the source of truth** for which ADRs exist. Always update it.
- **Do not commit.** Stage only. The user commits manually so they can review the diff.

## Anti-patterns to avoid

- Don't reuse a number. If `docs/adr/0005-*.md` exists, the next one is `0006`, never `0005a` or `0005-v2`.
- Don't auto-fill Decision/Context with boilerplate. Leave them as the template placeholders so the user must write the actual content.
- Don't add the ADR to the "Pending" section of README — Pending is for ADRs known to be coming in a specific week but not yet written.
