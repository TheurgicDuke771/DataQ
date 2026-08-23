"""Reading a credential's own expiry, where the credential carries one (#838)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

# A SAS is identified by its signature plus an expiry — both are mandatory in every SAS Azure
# issues, and no other credential DataQ holds is `&`-delimited key=value pairs containing them.
_SAS_REQUIRED = ("sig", "se")

# On a *user-delegation* SAS the signing key has its own lifetime (`ske`), and the service rejects
# the token once EITHER has passed — so the effective expiry is the earlier of the two.
_SAS_EXPIRY_FIELDS = ("se", "ske")


def azure_sas_expiry(secret: str) -> datetime | None:
    """The moment an Azure storage SAS stops working, or ``None``."""
    if not secret:
        return None
    fields = parse_qs(secret.lstrip("?"), keep_blank_values=False)
    if not all(key in fields for key in _SAS_REQUIRED):
        return None

    expiries = [
        parsed
        for key in _SAS_EXPIRY_FIELDS
        for raw in fields.get(key, [])
        if (parsed := _parse_sas_time(raw)) is not None
    ]
    return min(expiries) if expiries else None


def _parse_sas_time(raw: str) -> datetime | None:
    """One SAS ISO-8601 time → aware UTC datetime, or ``None`` if unparseable."""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
