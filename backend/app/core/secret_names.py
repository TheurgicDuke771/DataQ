"""Human-readable SecretStore key names: ``conn-<type>-<qualifier>-<env>-<short-id>``
(the slug makes a key findable — #954; the short id makes renames collision-free).
Charset is dictated by the STRICTEST backend: Azure Key Vault's ``[0-9a-zA-Z-]``,
127-char limit — one name must stay valid in every store behind the seam (ADR 0010).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final
from uuid import UUID

# Key Vault's alphabet — the tightest of the supported backends.
_ALLOWED: Final = re.compile(r"[^0-9a-zA-Z-]+")
_DASHES: Final = re.compile(r"-{2,}")

# Slug bound keeps the total well inside Key Vault's 127; the id suffix guarantees
# uniqueness, so truncation costs readability, never correctness.
_MAX_SLUG: Final = 60
# 8 hex chars of a UUID4 — unique enough among connections sharing a slug.
_ID_CHARS: Final = 8

# Hard bound on free text the regexes scan — makes the O(N²) safety unconditional
# rather than a property of the CALLER's 128-char validation (this is a library).
_MAX_INPUT: Final = 256

# How a connection type appears in a vault key (mirrors the hand-chosen prod names).
_TYPE_SLUGS: Final = {
    "adls_gen2": "adls",
    "unity_catalog": "unity-catalog",
}

# Words a DISPLAY NAME uses for each type, stripped from the qualifier — the overlap
# is rarely literal ("Azure Data Factory" ↔ `adf`), which is why this table exists.
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


def _split_parentheticals(text: str) -> tuple[str, str]:
    """Split `text` into (outside-parens, inside-parens) in ONE linear pass. The
    obvious regex (`\\([^)]*\\)`) is quadratic on unmatched "(" — ~115 ms at 20k chars
    of user text — so a scan is used by construction, not by input bound.
    """
    outside: list[str] = []
    inside: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth:
            inside.append(ch)
        else:
            outside.append(ch)
    return "".join(outside), "".join(inside)


def slugify(text: str) -> str:
    """Reduce free text to Key Vault's ``[0-9a-zA-Z-]`` alphabet, transliterating
    Unicode where possible (``Ünïcodé`` → ``Unicode``) rather than dropping it.
    """
    # NFKD splits accented chars into base + combining mark; drop the marks.
    decomposed = unicodedata.normalize("NFKD", text[:_MAX_INPUT])
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = _ALLOWED.sub("-", ascii_only)
    slug = _DASHES.sub("-", slug).strip("-").lower()
    return slug[:_MAX_SLUG].strip("-")


def connection_secret_ref(
    *, connection_id: UUID | str, env: str, name: str, conn_type: str = "", kind: str = ""
) -> str:
    """Build the vault key ``conn-<type>-<qualifier>-[<kind>-]<env>-<shortid>`` (``kind`` names a
    SECOND credential on the same row, e.g. ``"catalog"`` — #754/#826/#1181). Call ONLY when
    minting a new ref: an existing `Connection.secret_ref` is authoritative — recomputing after
    a rename points at a key that does not exist (#954).
    """
    # The one input that never passes `slugify`; an empty/dash-only id would emit a
    # trailing dash, which Key Vault rejects at the API.
    short_id = _ALLOWED.sub("", str(connection_id).replace("-", ""))[:_ID_CHARS]
    type_slug = _TYPE_SLUGS.get(conn_type, slugify(conn_type))

    # Parenthesised commentary is detail for humans, not identity.
    bounded = name[:_MAX_INPUT]
    noise = set(_TYPE_WORDS.get(conn_type, ())) | set(type_slug.split("-"))
    outside, inside = _split_parentheticals(bounded)

    def _qualify(text: str) -> list[str]:
        return [t for t in slugify(text).split("-") if t and t not in noise]

    qualifier = _qualify(outside)
    if not qualifier:
        # Everything distinguishing lived inside the parentheses ("Snowflake (Retail)") — dropping
        # it would reinstate the #954 find-the-right-entry problem.
        qualifier = _qualify(inside)

    # `head` is free text and truncatable; `tail` (kind/env) must survive intact.
    head = list(dict.fromkeys(["conn"] + (type_slug.split("-") if type_slug else []) + qualifier))
    tail: list[str] = []
    seen = set(head)
    for token in (slugify(kind), slugify(env)):
        if token and token not in seen:
            tail.append(token)
            seen.add(token)

    tail_str = "-".join(tail)
    # Reserve the tail's exact width out of the shared budget; the remainder goes to
    # the truncatable head. max(..., 0) is a safety floor only.
    head_budget = max(_MAX_SLUG - len(tail_str) - (1 if tail_str else 0), 0)
    head_str = "-".join(head)[:head_budget].strip("-")
    slug = "-".join(part for part in (head_str, tail_str) if part)
    # .strip("-") on the JOINED result: an id filtered to nothing would leave a
    # trailing dash, which Key Vault rejects at the API — a 500 on save.
    return "-".join(part for part in (slug, short_id) if part).strip("-") or "conn"


def is_readable_ref(ref: str) -> bool:
    """True only when `ref` has the shape THIS module mints (ends in `-<hex id>`).
    Deliberately NOT "does not parse as a UUID": that weaker test counted the
    hand-curated legacy names as already-migrated, and the migration keys its
    idempotency off this predicate.
    """
    if not ref.startswith("conn-"):
        return False
    tail = ref.rsplit("-", 1)[-1]
    return bool(tail) and len(tail) <= _ID_CHARS and all(c in "0123456789abcdef" for c in tail)
