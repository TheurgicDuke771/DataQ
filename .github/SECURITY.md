# Security Policy

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.** Public disclosure before a fix is ready puts all users at risk.

Report vulnerabilities privately via **[GitHub Security Advisories](https://github.com/TheurgicDuke771/DataQ/security/advisories/new)**.

You will receive a response within **5 business days** acknowledging the report. We aim to release a fix within **30 days** for critical issues and **90 days** for lower-severity issues, depending on complexity.

## What to include

- A clear description of the vulnerability and the affected component
- Steps to reproduce (or a proof-of-concept)
- Potential impact and severity assessment
- Any suggested mitigations

## Scope

Components in scope:
- FastAPI backend (`/api/v1/*`, `/mcp`)
- Azure AD authentication flow (MSAL token validation)
- Celery worker + GX execution path
- Key Vault secret access patterns

Out of scope:
- Vulnerabilities in third-party dependencies — report those upstream; we track them via Dependabot
- Issues requiring physical access to Azure infrastructure

## Supported versions

Only the latest commit on `main` is supported. There are no versioned releases in the v1 development phase.
