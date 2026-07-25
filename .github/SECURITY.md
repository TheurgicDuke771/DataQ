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
- Authentication flow — generic OIDC sign-in (`oidc-client-ts`) + backend token validation, and personal access tokens
- Celery worker + GX execution path
- Secret-store access patterns (the `SecretStore` seam; Azure Key Vault is the validated implementation)

Out of scope:
- Vulnerabilities in third-party dependencies — report those upstream; we track them via Dependabot
- Issues requiring physical access to the infrastructure a deployment runs on

## Supported versions

DataQ follows [Semantic Versioning](https://semver.org/), with releases tagged in this
repository and curated in [CHANGELOG.md](../CHANGELOG.md).

| Version | Supported |
|---|---|
| `main` (latest commit) | Yes — fixes land here first |
| Latest tagged release (currently **v1.0.0**) | Yes |
| Older tagged releases | No |

Security fixes are applied to `main` and ship in the next release. There is no long-term
support branch and no backporting to superseded tags — if you are running an older tag,
upgrade to the latest release to pick up a fix.
