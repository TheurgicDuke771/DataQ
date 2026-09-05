"""Email identity helpers shared by every auth door.

Imports nothing from DataQ, so the auth seam, the OTP service and the membership
service can all use them without an import cycle.
"""

from __future__ import annotations

import hashlib


def normalize_email(email: str) -> str:
    """The ONE email normalization rule: strip + lower."""
    return email.strip().lower()


def identity_log_fields(email: str) -> dict[str, str]:
    """Log fields naming an identity without logging the address itself."""
    # Normalized here so any casing of the address reproduces the same digest.
    normalized = normalize_email(email)
    _, _, domain = normalized.partition("@")
    return {
        "email_domain": domain or "(none)",
        "email_digest": hashlib.sha256(normalized.encode()).hexdigest()[:12],
    }


def allowlisted(email: str, emails: frozenset[str], domains: frozenset[str]) -> bool:
    """Whether an EXPLICIT allowlist entry names `email`.

    Exact address or exact domain, never a suffix: `acme.io` must not admit
    `evil-acme.io` or `acme.io.evil.net`.
    """
    normalized = normalize_email(email)
    if normalized in emails:
        return True
    _, _, domain = normalized.partition("@")
    return bool(domain) and domain in domains
