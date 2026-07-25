"""Reading a credential's own expiry, where the credential carries one (#838).

Prod lineage was dark for six days because an ADLS SAS expired. #828 made that
failure *visible once it happened*; this module is the other half — the expiry
was knowable the whole time, printed inside the token itself.

**Parse, never guess.** Every function here returns ``None`` unless the string is
unmistakably the credential shape it claims to read. A credential whose expiry we
cannot know is silent (AC 2 of #838): a wrong date is worse than no date, because
a "expires in 90 days" badge on a token that dies tomorrow is an outage with a
false alibi.

**The secret never leaves this module.** Callers hand in the credential and get
back a ``datetime`` — the value is never logged, never returned in an error, and
never stored. A parse failure returns ``None`` rather than raising, precisely so
no caller is tempted to put the offending string in an exception message (the
#536 traceback-locals leak is the precedent).

Only Azure storage SAS is implemented today, because it is the only credential
DataQ holds whose expiry is *in the credential*: S3 access keys, Snowflake
key-pairs, and Databricks PATs carry no expiry, so their adapters stay silent.
A JWT-shaped credential (``exp`` claim) is the natural next implementation and
belongs here rather than in an adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

# A SAS is identified by its signature plus an expiry — both are mandatory in
# every SAS Azure issues, and no other credential DataQ holds is `&`-delimited
# key=value pairs containing them. Requiring `sig` is what keeps this a parse
# rather than a guess: a bare query string that happens to carry `se=` is not
# treated as a credential.
_SAS_REQUIRED = ("sig", "se")

# On a *user-delegation* SAS the signing key has its own lifetime (`ske`), and the
# service rejects the token once EITHER has passed — so the effective expiry is
# the earlier of the two. Reading only `se` would over-promise on exactly the
# credential kind Azure recommends.
_SAS_EXPIRY_FIELDS = ("se", "ske")


def azure_sas_expiry(secret: str) -> datetime | None:
    """The moment an Azure storage SAS stops working, or ``None``.

    ``None`` means "this is not a SAS, or its expiry is unreadable" — never
    "it does not expire". Returns a timezone-aware UTC datetime; SAS times are
    always UTC, and a naive one is read as such.
    """
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
    """One SAS ISO-8601 time → aware UTC datetime, or ``None`` if unparseable.

    Deliberately total: an unparseable value yields ``None`` (which makes the
    whole credential silent) rather than raising, so a malformed token can never
    surface its own text through an exception.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
