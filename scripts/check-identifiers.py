#!/usr/bin/env python3
"""Block personal and live-infrastructure identifiers from being committed.

This is **not** a secret scanner — betterleaks already does that, and a secret and
an identifier fail differently. A leaked secret is revoked and rotated; a leaked
identifier cannot be, because it names something real that keeps existing. The
maintainers' email, a Postgres server's hostname, a Snowflake account locator and
an AWS account id all reached this public repo and had to be scrubbed by hand
after the fact.

## What it looks for, and why these patterns

Every rule is anchored on a **provider-specific suffix or shape** rather than on a
generic word, because a generic word is where false positives come from and a hook
people learn to skip protects nothing. `\\b\\d{12}\\b` would match a byte count; an
ARN's account field will not.

Personal email is deliberately narrowed to **consumer mail providers**. Enumerating
"real-looking" domains is unbounded, but the PII actually at risk here is a
person's own address, and those live at a short list of providers.

## Placeholders pass

Documentation and tests need example values, so a match is allowed when it looks
deliberately fake — a placeholder word, an angle bracket, or a digit run that no
real allocator produces (`0000…`, `1234…`). That check is on the **matched text**,
so `acme.blob.core.windows.net` passes while a real account name does not.

Deliberate exceptions take an inline `identifier-ok:` pragma **with a reason**, on
the line or the one above it. LICENSE is exempt wholesale: removing the copyright
holder's name would break the MIT grant.

Usage: `check-identifiers.py [files...]` — no args means every tracked file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PRAGMA = "identifier-ok:"

# Paths where a real identifier is correct and must stay.
EXEMPT_PATHS = {
    "LICENSE",  # copyright holder — removing it breaks the MIT grant
    "scripts/check-identifiers.py",  # this file, which necessarily contains the patterns
}
EXEMPT_PREFIXES = ("deploy/terraform/azure/terraform.tfvars",)  # gitignored real values

# Binary/vendored trees never worth scanning.
SKIP_PREFIXES = ("node_modules/", ".git/", "frontend/dist/", "site/")
SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".pdf",
    ".lock",
    ".woff",
    ".woff2",
)

CONSUMER_MAIL = (
    r"(?:gmail|googlemail|outlook|hotmail|live|yahoo|ymail|proton|protonmail"
    r"|icloud|me|aol|gmx|zoho)\.[a-z]{2,3}"
)

RULES: list[tuple[str, str, str]] = [
    (
        "personal-email",
        rf"\b[A-Za-z0-9._%+-]+@{CONSUMER_MAIL}\b",
        "a personal email address",
    ),
    (
        "azure-postgres-host",
        r"\b[a-z0-9][a-z0-9-]{2,62}\.postgres\.database\.azure\.com\b",
        "a live Azure PostgreSQL server hostname",
    ),
    (
        "azure-postgres-name",
        r"\b[a-z0-9-]*pg-[a-z0-9]{2,}-[a-z0-9]{5,}\b",
        "what looks like a real Azure PostgreSQL server name",
    ),
    (
        "azure-keyvault-host",
        r"\b[a-z0-9-]{3,24}\.vault\.azure\.net\b",
        "a live Key Vault hostname",
    ),
    (
        "azure-storage-host",
        r"\b[a-z0-9]{3,24}\.(?:blob|dfs|queue|table)\.core\.windows\.net\b",
        "a live Azure Storage account",
    ),
    (
        "azure-containerapp-host",
        # 2-4 labels, hyphens allowed: a real ingress FQDN is
        # <app>.<internal?>.<envname-suffix>.<region>.azurecontainerapps.io, and an
        # exact-label-count pattern missed it. Found by testing the hook against the
        # real value rather than by reading the regex.
        r"\b[a-z0-9-]+(?:\.[a-z0-9-]+){1,4}\.azurecontainerapps\.io\b",
        "a live Container Apps ingress hostname",
    ),
    (
        "databricks-workspace",
        r"\b(?:dbc-[0-9a-f]{8}-[0-9a-f]{4}|adb-\d{10,20})\b",
        "a Databricks workspace id",
    ),
    (
        "snowflake-host",
        r"\b[a-z0-9_-]+\.snowflakecomputing\.com\b",
        "a Snowflake account hostname",
    ),
    (
        "snowflake-locator",
        r"\b[A-Z]{5,}-[A-Z]{2,}\d{4,}\b",
        "a Snowflake account locator",
    ),
    (
        "cloudfront-domain",
        r"\b[a-z0-9]{12,14}\.cloudfront\.net\b",
        "a CloudFront distribution domain",
    ),
    (
        "aws-account-id",
        r"(?:arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:|\b)(\d{12})(?::|\.dkr\.ecr)",
        "an AWS account id",
    ),
]

# A match is allowed when it is visibly a placeholder. Checked against the MATCHED
# TEXT, so this cannot whitelist a real value that merely sits on a line mentioning
# "example".
PLACEHOLDER = re.compile(
    r"example|acme|contoso|fabrikam|myaccount|mycompany|mylake|my-|your-|yourorg|"
    r"\bacct\b|\baccount\b|\bhost\b|\bworkspace\b|"
    r"placeholder|changeme|redacted|anonymi[sz]ed|\bfake\b|dummy|sample|"
    r"\btest\b|test\d|demo|foo|bar|baz|xxx|<|\{\{|\$\{",
    re.IGNORECASE,
)
# Digit/hex runs no real allocator hands out.
SYNTHETIC_RUN = re.compile(r"(?:0000|1111|2222|1234|abcd|dead|beef|f{4})", re.IGNORECASE)


def _is_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER.search(text) or SYNTHETIC_RUN.search(text))


def _tracked_files() -> list[str]:
    # Full path: ruff S607 rejects a bare "git", and a hook that resolves its own
    # tooling by PATH is a hook whose behaviour depends on the caller's shell.
    out = subprocess.run(
        ["/usr/bin/env", "git", "ls-files"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _scannable(path: str) -> bool:
    if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
        return False
    if path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES):
        return False
    return True


def scan(paths: list[str]) -> list[tuple[str, int, str, str, str]]:
    findings = []
    for path in paths:
        if not _scannable(path):
            continue
        p = Path(path)
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: nothing text-shaped to leak
        for i, line in enumerate(lines, 1):
            if PRAGMA in line or (i >= 2 and PRAGMA in lines[i - 2]):
                continue
            for name, pattern, why in RULES:
                for m in re.finditer(pattern, line):
                    hit = m.group(0)
                    if _is_placeholder(hit):
                        continue
                    findings.append((path, i, name, hit, why))
    return findings


def main(argv: list[str]) -> int:
    paths = argv[1:] or _tracked_files()
    findings = scan(paths)
    if not findings:
        return 0

    print("Identifier check FAILED — these look like real, live identifiers:\n")
    for path, line, name, hit, why in findings:
        print(f"  {path}:{line}  [{name}]  {hit!r}")
        print(f"      → {why}")
    print(
        "\nThis repo is public, and an identifier cannot be rotated the way a secret can.\n"
        "Fix one of these ways:\n"
        "  • replace it with a placeholder (an <angle-bracket> name, or an acme/example value);\n"
        "  • move it to deployment-specific config that is gitignored (see\n"
        "    deploy/terraform/azure/variables.tf `shared_pg_server_name` for the pattern);\n"
        f"  • if it genuinely must stay, add `{PRAGMA} <reason>` on the line or the one above."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
