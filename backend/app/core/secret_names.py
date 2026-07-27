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

# Parenthesised commentary — "(DATAQ_READER)", "(flat files)", "(harness)".
_PARENS: Final = re.compile(r"\([^)]*\)")

# How a connection type appears in a vault key. Mirrors the hand-chosen prod names
# (`conn-adls-landing`, not `conn-adls-gen2-landing`).
_TYPE_SLUGS: Final = {
    "adls_gen2": "adls",
    "unity_catalog": "unity-catalog",
}

# The words a DISPLAY NAME uses for each type, stripped from the qualifier so the
# type is not repeated. Rarely a literal match for the type key — a user writes
# "Azure Data Factory", the type is `adf` — which is why this table exists.
_TYPE_WORDS: Final = {
    "adf": ("azure", "data", "factory"),
    "adls_gen2": ("adls", "gen2", "azure", "storage"),
    "airflow": ("apache", "airflow"),
    "unity_catalog": ("unity", "catalog", "databricks"),
    "snowflake": ("snowflake",),
    "iceberg": ("iceberg", "apache"),
    "dbt": ("dbt",),
    "s3": ("s3", "aws", "amazon"),
}


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


def connection_secret_ref(
    *, connection_id: UUID | str, env: str, name: str, conn_type: str = ""
) -> str:
    """Build the vault key for a connection's primary credential.

    Shape: ``conn-<type>-<qualifier>-<env>-<shortid>``, with redundant parts
    dropped. Modelled on the names an operator had already chosen by hand for 11
    of the 13 production secrets (`conn-snowflake-retail`, `conn-adf-qa`, …) —
    those were curated, so the generator earns its keep only by reproducing that
    quality automatically.

    Three rules do the work, and each exists because the naive version produced a
    name *worse* than what it replaced:

    1. **Type leads.** Grouping by type is what makes a vault listing scannable
       (`conn-adf-*`, `conn-snowflake-*`), and it is what the hand-naming did.
    2. **Type words are stripped from the qualifier.** "Snowflake — Retail" under
       type `snowflake` must not yield `snowflake-snowflake-retail`. `_TYPE_WORDS`
       maps a type to the words a display name uses for it, because the overlap is
       rarely literal (`adf` ↔ "Azure Data Factory").
    3. **Parentheticals are dropped and `env` is deduplicated.** "(DATAQ_READER)"
       is commentary, and "Azure Data Factory — QA" in env `qa` must not become
       `…-qa-qa-…`.

    Call this ONLY when minting a new ref. An existing `Connection.secret_ref` is
    authoritative and must be reused verbatim — recomputing it after a rename
    would point at a key that does not exist, and the credential would read as
    missing (the #954 shape again, self-inflicted).
    """
    short_id = str(connection_id).replace("-", "")[:_ID_CHARS]
    type_slug = _TYPE_SLUGS.get(conn_type, slugify(conn_type))

    # Drop parenthesised commentary before slugging: it is detail for humans
    # reading the connection list, not identity.
    bare_name = _PARENS.sub(" ", name)
    noise = set(_TYPE_WORDS.get(conn_type, ())) | set(type_slug.split("-"))
    qualifier = [t for t in slugify(bare_name).split("-") if t and t not in noise]

    parts = ["conn"]
    parts += type_slug.split("-") if type_slug else []
    parts += qualifier
    env_slug = slugify(env)
    if env_slug and env_slug not in parts:
        parts.append(env_slug)

    # Dedupe while preserving order, then bound the readable half — the id is what
    # guarantees uniqueness, so truncation costs readability, never correctness.
    ordered = list(dict.fromkeys(parts))  # dedupe, order-preserving
    slug = "-".join(ordered)[:_MAX_SLUG].strip("-")
    return f"{slug}-{short_id}" if slug else f"conn-{short_id}"


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
