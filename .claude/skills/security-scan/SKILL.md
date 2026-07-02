---
name: security-scan
description: Run the end-of-week security scan (CONTRIBUTING rule 36) — Dependabot + secret-scanning alerts, local pip-audit/pnpm-audit mirror of the CI gates, betterleaks sweep, OWASP spot check on endpoints added this week, Key Vault access audit, and the credential-rotation register. Use weekly, before a deploy, or when the user asks "run the security scan" / "any open vulns?".
disable-model-invocation: true
---

# security-scan

## Purpose

Operationalize CONTRIBUTING rule 36 (end-of-week quick scan) into one repeatable checklist. Read-only against the repo and GitHub; never fixes anything itself — findings get routed per the Rules at the bottom.

Optional arg: `--since <YYYY-MM-DD>` — start of the review window (default: 7 days ago).

## Steps

Run every step even if an early one fails; the value is the complete weekly picture. Mark steps you couldn't run as ⚠️ SKIPPED with the reason.

### 1. Dependabot vulnerability alerts (async layer)

```bash
gh api 'repos/TheurgicDuke771/DataQ/dependabot/alerts?state=open&per_page=100' \
  --jq '.[] | {pkg: .dependency.package.name, eco: .dependency.package.ecosystem, sev: .security_advisory.severity, cve: .security_advisory.cve_id, summary: .security_advisory.summary}'
```

Empty output = clean. For each open alert, note whether a Dependabot PR already exists (`gh pr list --author app/dependabot`).

### 2. GitHub secret-scanning alerts

```bash
gh api 'repos/TheurgicDuke771/DataQ/secret-scanning/alerts?state=open' --jq '.[] | {type: .secret_type_display_name, url: .html_url}'
```

A 404 means the feature isn't enabled for the repo — report that as a ⚠️ finding itself, don't silently skip.

### 3. Local dependency audit (mirror of the synchronous CI gate)

```bash
pip-audit -r backend/requirements-dev.txt
cd frontend && pnpm audit --audit-level=high
```

These are the same commands CI runs as a merge gate — running them here catches vulns published since the last PR merged.

### 4. Secret scan sweep (full tree, not incremental)

```bash
pre-commit run betterleaks --all-files
```

CI scans incrementally (only new commits); this weekly full sweep is the backstop. Also eyeball `git log --since <window> --stat` for any new tracked file that looks credential-shaped (templates must ship secret keys blank — CLAUDE.md §11).

### 5. OWASP spot check on new/changed endpoints

List API surface changed in the window:

```bash
git log --since <window> --name-only --pretty=format: -- backend/app/api/ backend/app/mcp/ | sort -u
```

For each changed router, check (read the code, don't guess):
- **Authz:** route depends on `get_current_user` and suite-scoped access where applicable (owned-or-shared, or `require_workspace_admin`); no generic-identity bypass.
- **Input validation:** Pydantic-validated request bodies; no raw dict passthrough into services; custom-SQL paths keep the read-only single-statement guardrails (ADR 0019).
- **Error shape:** failures return the standard error envelope, never a stack trace or connection string (the #536 traceback-locals leak is the cautionary tale).
- **PII:** anything returning sample rows goes through the column-aware redaction path (#417); nothing logs sample data outside the logger-level redactor.
- **Webhook surfaces** (`/orchestration/events/*`): auth still enforced (ADF shared secret, Airflow HMAC), and payloads treated as hostile input.

### 6. Key Vault access audit (needs `az login`; ⚠️ SKIP with reason if not logged in)

```bash
az keyvault list --resource-group dataq-rg --query '[].name' -o tsv
az role assignment list --scope $(az keyvault list --resource-group dataq-rg --query '[0].id' -o tsv) \
  --query '[].{who:principalName, role:roleDefinitionName}' -o table
```

Flag any principal that isn't the app's user-assigned managed identity, the deploy CI identity, or the owner. Also check the KV purge-protection tfvars decision if still open.

### 7. Credential-rotation register

Check expiry/rotation status of the live credentials (see the connections memory / Key Vault):
- **Snowflake PAT** — 90-day lifetime; compute days remaining from last rotation.
- **ADLS account SAS** — expires 2027-06-28.
- **Databricks PAT** — rotation was REQUIRED after the #536/#538 traceback-locals leak; verify it happened.
- Webhook shared secret (ADF) + Airflow HMAC signing key — rotate on any suspicion of exposure (hard-cutover per ADR 0006).

Flag anything expiring within 30 days or with an unconfirmed required rotation.

## How to report

One summary table — step → ✅ clean / 🔴 finding(s) / ⚠️ skipped(reason) — followed by details per finding (source, severity, affected package/endpoint/credential, suggested next action). End with an explicit verdict: `Clean`, `N findings — action needed`, or `Incomplete — N steps skipped`.

## Rules

- **Security vulnerabilities are never public GitHub issues** (rule 38). Route exploitable findings to GitHub Security Advisories: https://github.com/TheurgicDuke771/DataQ/security/advisories/new (the `/gh-issue-from-finding` skill's `--security` flag does this).
- **Non-sensitive hardening items** (e.g. "add rate limiting", "enable secret scanning") → `/gh-issue-from-finding` as normal issues.
- **This skill never modifies anything** — no dep bumps, no config changes, no rotations. It reports; remediation is separate, tracked work.
- Don't paste secret values, tokens, or full connection strings into the report — name the credential, not its value.
