"""Column classification for failing-sample redaction (#415)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any


class ColumnClass(StrEnum):
    """How a column's values may be surfaced in a failing-row sample."""

    IDENTIFIER = "identifier"  # non-person locator — SHOW (so a failing row is findable)
    PII = "pii"  # personal / sensitive — MASK
    SAFE = "safe"  # non-sensitive metric/time/status — SHOW when relevant


# ── Name-token vocabularies (matched against the column's word tokens) ────────── A person token →
# PII, unconditionally (no entity qualifier can un-flag these.
_PERSON_TOKENS: frozenset[str] = frozenset(
    {
        "email",
        "mail",
        "phone",
        "mobile",
        "cell",
        "fax",
        "contact",
        "ssn",
        "sin",
        "nid",
        "passport",
        "license",
        "licence",
        "dob",
        "birth",
        "birthdate",
        "birthday",
        "gender",
        "username",
        "login",
        "password",
        "iban",
        "swift",
        "bic",
        "cvv",
        "cvc",
        "aadhaar",
    }
)
# Address-class tokens (#1182): quasi-identifying, but context-dependent — a person's
# ``city``/``address`` is PII, but the SAME token on a facility/geographic entity
_ADDRESS_TOKENS: frozenset[str] = frozenset(
    {"address", "street", "city", "zip", "zipcode", "postal", "postcode"}
)
# Financial domains whose *number* is direct PII — ``account_number`` / ``card_no`` /
# ``routing_number``.
_FINANCIAL_DOMAINS: frozenset[str] = frozenset(
    {"account", "card", "credit", "debit", "cc", "routing", "sort"}
)
_NUMBER_TOKENS: frozenset[str] = frozenset({"number", "no", "num"})
# Government-identifier domains where the *id itself* is the sensitive number —
# ``tax_id`` / ``national_id`` / ``vat_number`` → PII with any id-suffix.
_NATIONAL_ID_DOMAINS: frozenset[str] = frozenset(
    {"tax", "vat", "national", "ssn", "sin", "tin", "nino"}
)
# Explicit *person-name* tokens (so bare ``name`` on product_name/file_name is spared).
_PERSON_NAME_TOKENS: frozenset[str] = frozenset(
    {"firstname", "lastname", "fullname", "surname", "forename", "givenname", "middlename"}
)
# Tokens that mark the surrounding column as belonging to a SPECIFIC PERSON (a
# customer/recipient/etc.), not a generic entity (#1182 review finding).
_PERSON_CONTEXT_TOKENS: frozenset[str] = frozenset(
    {
        "customer",
        "user",
        "member",
        "recipient",
        "shipper",
        "shipping",
        "delivery",
        "pickup",
        "patient",
        "client",
        "guest",
        "buyer",
        "billing",
        "employee",
        "resident",
        "tenant",
        "subscriber",
        "passenger",
        "applicant",
        "owner",
        "contact",
    }
)
# Entities that own a *non-person* ``name`` — product_name, category_name, … are labels, not PII.
_NON_PERSON_ENTITIES: frozenset[str] = frozenset(
    {
        "product",
        "category",
        "brand",
        "file",
        "column",
        "table",
        "node",
        "supplier",
        "vendor",
        "carrier",
        "channel",
        "store",
        "warehouse",
        "region",
        "country",
        "currency",
        "status",
        "type",
        "event",
        "step",
        "role",
        "tag",
        "location",
    }
)
# Non-person *id-suffix* tokens — safe to SHOW as a row locator.
_IDENTIFIER_TOKENS: frozenset[str] = frozenset(
    {
        "id",
        "uuid",
        "guid",
        "key",
        "code",
        "number",
        "no",
        "num",
        "ref",
        "reference",
        "sku",
        "isbn",
        "upc",
        "ean",
        "serial",
        "barcode",
        "slug",
    }
)
# Metric / time / status tokens — non-sensitive, SHOW when relevant.
_SAFE_TOKENS: frozenset[str] = frozenset(
    {
        "ts",
        "at",
        "time",
        "timestamp",
        "date",
        "datetime",
        "day",
        "month",
        "year",
        "created",
        "updated",
        "modified",
        "loaded",
        "load",
        "amount",
        "amt",
        "qty",
        "quantity",
        "count",
        "total",
        "sum",
        "price",
        "cost",
        "fee",
        "rate",
        "tax",
        "discount",
        "balance",
        "score",
        "rating",
        "sentiment",
        "pct",
        "percent",
        "ratio",
        "status",
        "state",
        "flag",
        "kind",
        "method",
        "currency",
        "channel",
        "carrier",
        "enabled",
        "active",
        "valid",
        "deleted",
    }
)


def _tokens(name: str) -> list[str]:
    """Lowercase word tokens of a column name (``ORDER_NUMBER`` → ``['order', 'number']``,
    ``customerEmail`` → ``['customer', 'email']``). camelCase and snake/kebab both split.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _name_signal(name: str) -> ColumnClass | None:
    """Classify from the column *name* alone, or ``None`` if the name is inconclusive."""
    tokens = set(_tokens(name))
    if not tokens:
        return None

    # 1. Person / sensitive tokens, or an explicit person-name token → PII. Always —
    #    no entity qualifier un-flags an email/phone/ssn.
    if tokens & _PERSON_TOKENS or tokens & _PERSON_NAME_TOKENS:
        return ColumnClass.PII

    # A person-context qualifier (customer/delivery/shipping/…) always wins over a non-person entity
    # qualifier below (#1182 review finding): `delivery_location_zip` and `customer_location_addres
    person_qualified = bool(tokens & _PERSON_CONTEXT_TOKENS)

    # 1b.
    if tokens & _ADDRESS_TOKENS:
        if person_qualified:
            return ColumnClass.PII
        return ColumnClass.SAFE if tokens & _NON_PERSON_ENTITIES else ColumnClass.PII
    # Bare ``name``: PII by default (a person's name), but SAFE when it labels a non-person entity
    # (product_name / category_name are labels, not personal data).
    if "name" in tokens:
        if person_qualified:
            return ColumnClass.PII
        return ColumnClass.SAFE if tokens & _NON_PERSON_ENTITIES else ColumnClass.PII

    # 2.
    if (tokens & _FINANCIAL_DOMAINS) and (tokens & _NUMBER_TOKENS):
        return ColumnClass.PII
    if (tokens & _NATIONAL_ID_DOMAINS) and (tokens & _IDENTIFIER_TOKENS):
        return ColumnClass.PII

    # 3.
    if tokens & _IDENTIFIER_TOKENS:
        return ColumnClass.IDENTIFIER
    # 4. Metric / time / status → safe.
    if tokens & _SAFE_TOKENS:
        return ColumnClass.SAFE
    return None


# ── Value-shape signals (refine a name-inconclusive column) ─────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HASH_LENGTHS = frozenset({32, 40, 56, 64, 96, 128})  # md5/sha1/sha224/sha256/sha384/sha512 hex


def _shannon_entropy(text: str) -> float:
    """Shannon entropy (bits/char) of a string — high for random/encoded blobs."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def _looks_like_hash(value: str) -> bool:
    """A hex string of a common digest length (md5/sha*) — a hashed/opaque token."""
    return len(value) in _HASH_LENGTHS and bool(_HEX_RE.match(value))


def _looks_encoded(value: str) -> bool:
    """High-entropy base64/hex — an encrypted/encoded blob (treat as sensitive)."""
    return (
        len(value) >= 16
        and _shannon_entropy(value) > 3.5
        and bool(_BASE64_RE.match(value) or _HEX_RE.match(value))
    )


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _clean_values(values: Iterable[object]) -> list[str]:
    """Non-null, non-empty sampled values as strings."""
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s and s.upper() != "NULL":
            out.append(s)
    return out


# ── Full-population value-signal summary (#1230) ──────────────────────────────── `_value_signal`'s
# ratios (email ≥50%, id-shaped ≥80% + distinct-ratio ≥80%.
_SUMMARY_COUNT_KEYS = ("n", "email_count", "id_shaped_count", "encoded_count", "distinct_count")


def _value_signal_counts(values: Iterable[object]) -> dict[str, int] | None:
    """The raw counts `_value_signal`'s ratios are computed from — total non-null
    values (``n``), plus how many looked like an email / id-shaped / encoded value,
    and the distinct count. ``None`` when there are no non-null values (mirrors
    `_value_signal`'s own empty-sample contract).
    """
    cleaned = _clean_values(values)
    if not cleaned:
        return None
    return {
        "n": len(cleaned),
        "email_count": sum(_looks_like_email(v) for v in cleaned),
        "id_shaped_count": sum(_looks_like_uuid(v) or _looks_like_hash(v) for v in cleaned),
        "encoded_count": sum(_looks_encoded(v) for v in cleaned),
        "distinct_count": len(set(cleaned)),
    }


def value_signal_summary(values: Iterable[object]) -> dict[str, int] | None:
    """Public counts summary for persistence (#1230)."""
    return _value_signal_counts(values)


def _classify_counts(counts: Mapping[str, int]) -> ColumnClass | None:
    """`_value_signal`'s ratio logic, applied to pre-computed counts (either freshly
    derived from a sample, or a persisted `value_signal_summary`) rather than raw
    values directly — the shared tail both paths funnel through.
    """
    n = counts["n"]
    if n <= 0:
        return None
    if counts["email_count"] / n >= 0.5:
        return ColumnClass.PII
    if counts["id_shaped_count"] / n >= 0.8 and counts["distinct_count"] / n >= 0.8:
        return ColumnClass.IDENTIFIER
    if counts["encoded_count"] / n >= 0.5:
        return ColumnClass.PII
    return None


def _valid_summary(summary: Mapping[str, Any] | None) -> dict[str, int] | None:
    """Coerce/validate a persisted `value_signal_summary` sub-dict, or ``None`` if it's absent or
    malformed — an on-disk JSONB shape is never trusted blindly.
    """
    if not isinstance(summary, Mapping):
        return None
    try:
        counts = {key: int(summary[key]) for key in _SUMMARY_COUNT_KEYS}
    except (KeyError, TypeError, ValueError, OverflowError):
        # OverflowError: a persisted JSONB number can be a huge float (e.g. 1e400 -> inf) that
        # `int()` cannot convert — malformed the same way an out-of-range value is.
        return None
    if counts["n"] <= 0 or any(v < 0 for v in counts.values()):
        return None
    n = counts["n"]
    if any(counts[key] > n for key in _SUMMARY_COUNT_KEYS if key != "n"):
        return None
    return counts


def _value_signal(
    values: Sequence[object], *, value_signal_summary: Mapping[str, Any] | None = None
) -> ColumnClass | None:
    """Classify from a column's sampled *values*, or ``None`` if inconclusive."""
    counts = _valid_summary(value_signal_summary)
    if counts is None:
        counts = _value_signal_counts(values)
    if counts is None:
        return None
    return _classify_counts(counts)


def classify_column(
    name: str,
    sampled_values: Sequence[object] | None = None,
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> ColumnClass:
    """Classify a column as IDENTIFIER / PII / SAFE for sample redaction (#415)."""
    by_name = _name_signal(name)
    if by_name is ColumnClass.PII:
        return ColumnClass.PII
    by_value = _value_signal(sampled_values or [], value_signal_summary=value_signal_summary)
    if by_value is ColumnClass.PII:  # sensitive values override a name-based identifier
        return ColumnClass.PII
    if by_name is not None:  # IDENTIFIER or SAFE
        return by_name
    if by_value is not None:  # IDENTIFIER
        return by_value
    return ColumnClass.PII


def is_sensitive(
    name: str,
    sampled_values: Sequence[object] | None = None,
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a column is **affirmatively** PII — a person/sensitive name token or a
    directly-sensitive value signal (emails, encoded blobs) — as opposed to the
    conservative *default* mask.
    """
    return (
        _name_signal(name) is ColumnClass.PII
        or _value_signal(list(sampled_values or []), value_signal_summary=value_signal_summary)
        is ColumnClass.PII
    )
