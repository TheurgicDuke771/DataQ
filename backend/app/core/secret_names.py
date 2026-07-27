"""Human-readable names for SecretStore keys.

A secret name is what an operator reads when they open the vault to rotate a
credential by hand. `conn-<uuid>` told them nothing, and that is not a cosmetic
complaint: rotating a credential means finding the right entry first, and #954
(two dead Snowflake PATs, three weeks) is what "finding the right entry" costs
when every name is a UUID.

The generated shape is::

    conn-<env>-<name-slug>-<short-id>
    conn-dev-finance-warehouse-05c77ce3

**Both halves earn their place.** The slug is what makes it findable. The short
id is what makes a rename free: `secret_ref` is a STORED column, never
recomputed, so a renamed connection keeps its original secret name — and without
the id, renaming A→B while B's secret already exists would collide on a name
that is supposed to be unique. With it, two connections can never generate the
same key even if their names converge.

**The charset is dictated by the strictest backend, not the current one.** Azure
Key Vault permits only ``[0-9a-zA-Z-]`` (no underscores, dots or slashes) with a
127-char limit; OpenBao's KV v2 is far looser but treats ``/`` as path nesting.
Slugging to Key Vault's rules keeps one name valid in every store behind the
seam (ADR 0010) — a name generated under OpenBao must still be writable if the
deployment later moves to Key Vault, and vice versa.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final
from uuid import UUID

# Key Vault's alphabet, and the tightest of the backends we support.
_ALLOWED: Final = re.compile(r"[^0-9a-zA-Z-]+")
_DASHES: Final = re.compile(r"-{2,}")

# Bound the readable part so the total stays well inside Key Vault's 127. Connection
# names are free text and can be arbitrarily long; the id suffix is what guarantees
# uniqueness, so truncating the slug costs readability, never correctness.
_MAX_SLUG: Final = 60
# 8 hex chars of a UUID4: ~4.3e9 values, and it only has to be unique among the
# connections that share a slug — collision is not a practical concern.
_ID_CHARS: Final = 8


def slugify(text: str) -> str:
    """Reduce free text to Key Vault's ``[0-9a-zA-Z-]`` alphabet.

    Unicode is transliterated rather than dropped where possible (``Ünïcodé`` →
    ``Unicode``), so a non-ASCII connection name still yields something a human
    recognises instead of an empty string.
    """
    # NFKD splits accented characters into base + combining mark; dropping the
    # marks leaves the recognisable ASCII base letter.
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = _ALLOWED.sub("-", ascii_only)
    slug = _DASHES.sub("-", slug).strip("-").lower()
    return slug[:_MAX_SLUG].strip("-")


def connection_secret_ref(*, connection_id: UUID | str, env: str, name: str) -> str:
    """Build the vault key for a connection's primary credential.

    Call this ONLY when minting a new ref. An existing `Connection.secret_ref` is
    authoritative and must be reused verbatim — recomputing it after a rename
    would point at a key that does not exist, and the credential would read as
    missing (the #954 shape again, self-inflicted).
    """
    short_id = str(connection_id).replace("-", "")[:_ID_CHARS]
    slug = slugify(name)
    env_slug = slugify(env)
    # A name that slugs to nothing (e.g. entirely non-transliterable script) still
    # has to produce a valid, unique key — the id carries it.
    parts = [p for p in ("conn", env_slug, slug, short_id) if p]
    return "-".join(parts)


def is_readable_ref(ref: str) -> bool:
    """True when `ref` was minted by this module rather than the old UUID scheme.

    Used by the migration to stay idempotent: the legacy shape is
    ``conn-<36-char uuid>``, which is exactly 5 dash-separated groups after the
    prefix and carries no slug.
    """
    if not ref.startswith("conn-"):
        return False
    try:
        UUID(ref.removeprefix("conn-"))
    except ValueError:
        return True
    return False
