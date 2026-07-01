"""Column classification for failing-sample redaction (#415).

Classifies a column as **IDENTIFIER**, **PII**, or **SAFE** from its *name* plus a
handful of *sampled values*, so the sample redactor can surface a locating
identifier and the safe tested value while masking PII — instead of blanket-masking
every value (which makes a failing-row sample unactionable: you can't see *what* was
wrong or *which* row).

This is the **name-heuristic + value-signal** layer (#415 detection precedence step
2). Datasource-native classification (Snowflake ``privacy_category`` / UC column
tags) and an explicit suite override sit *above* it; both can overrule a guess here.

Design (adapted from a name-pattern + entropy/hash value heuristic — not lifted):

* **Name tokens give the primary signal.** A column whose name carries a *person*
  token (``email``/``phone``/``first_name``…) is PII; a *non-person* id token
  (``order_number``/``sku``/``tracking_number``…) is an IDENTIFIER; a metric / time /
  status token (``load_ts``/``amount``/``status``…) is SAFE.
* **Person-linking ids are identifiers.** ``customer_id`` / ``user_id`` are
  surrogate/pseudonymous keys — the ideal row locator, and they don't leak a direct
  identifier — so they are shown. The value signal still catches a *natural* key that
  holds PII (a ``user_id`` column of emails → PII), and an explicit override / tag can
  overrule for a stricter posture.
* **Value shape refines an otherwise-unknown column.** UUID/hash-shaped,
  high-cardinality values look like identifiers; high-entropy encrypted/hashed blobs
  are treated as sensitive.
* **Conservative default.** Anything not confidently IDENTIFIER or SAFE is PII, so the
  redactor's default-mask posture (security can't regress, #415) is preserved.

Pure, dependency-light, DB-free — unit-testable in isolation and reused by the
policy-derivation path (a later step wires it to auto-fill
``Suite.column_policy``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from enum import StrEnum


class ColumnClass(StrEnum):
    """How a column's values may be surfaced in a failing-row sample."""

    IDENTIFIER = "identifier"  # non-person locator — SHOW (so a failing row is findable)
    PII = "pii"  # personal / sensitive — MASK
    SAFE = "safe"  # non-sensitive metric/time/status — SHOW when relevant


# ── Name-token vocabularies (matched against the column's word tokens) ──────────
# A person token → PII. Bare ``name`` is handled specially (product_name is not a
# person), so it is NOT listed here; the person-name tokens below are explicit.
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
        "address",
        "street",
        "city",
        "zip",
        "zipcode",
        "postal",
        "postcode",
        "iban",
        "swift",
        "bic",
        "cvv",
        "cvc",
        "aadhaar",
    }
)
# Financial domains whose *number* is direct PII — ``account_number`` / ``card_no`` /
# ``routing_number``. Only the NUMBER: ``account_id`` / ``card_id`` are surrogate row
# FKs (locators, like ``customer_id``), so the id-suffix does NOT trip these.
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
# Entities that own a *non-person* ``name`` — product_name, category_name, … are labels,
# not PII.
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
    }
)
# Non-person *id-suffix* tokens — safe to SHOW as a row locator. Deliberately only the
# id-bearing tokens, NOT entity nouns (``order``/``invoice``/``batch``): ``order_number``
# is an identifier via ``number``, but ``order_ts`` is a timestamp (SAFE) — an entity
# noun alone must not force IDENTIFIER.
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
        "enabled",
        "active",
        "valid",
        "deleted",
    }
)


def _tokens(name: str) -> list[str]:
    """Lowercase word tokens of a column name (``ORDER_NUMBER`` → ``['order', 'number']``,
    ``customerEmail`` → ``['customer', 'email']``). camelCase and snake/kebab both split."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _name_signal(name: str) -> ColumnClass | None:
    """Classify from the column *name* alone, or ``None`` if the name is inconclusive.

    Precedence: person-PII → person-linking id (PII) → non-person identifier → safe.
    """
    tokens = set(_tokens(name))
    if not tokens:
        return None

    # 1. Person / sensitive tokens, or an explicit person-name token → PII.
    if tokens & _PERSON_TOKENS or tokens & _PERSON_NAME_TOKENS:
        return ColumnClass.PII
    # Bare ``name``: PII by default (a person's name), but SAFE when it labels a
    # non-person entity (product_name / category_name are labels, not personal data).
    if "name" in tokens:
        return ColumnClass.SAFE if tokens & _NON_PERSON_ENTITIES else ColumnClass.PII

    # 2. A sensitive-domain identifier is itself direct PII → MASK, checked before the
    #    generic id-suffix rule so ``number``/``id`` can't make it a shown locator:
    #    a financial *number* (account_number/card_no), or a government id (tax_id).
    #    A financial ``_id`` (account_id) is a surrogate FK, so it's excluded.
    if (tokens & _FINANCIAL_DOMAINS) and (tokens & _NUMBER_TOKENS):
        return ColumnClass.PII
    if (tokens & _NATIONAL_ID_DOMAINS) and (tokens & _IDENTIFIER_TOKENS):
        return ColumnClass.PII

    # 3. An id-suffix token → SHOW as a locator. This INCLUDES person-linking keys
    #    (customer_id, user_id): a surrogate/pseudonymous key is the ideal row locator
    #    and doesn't itself leak a direct identifier — showing it is the point of an
    #    actionable sample. A natural key that IS PII (a `user_id` holding emails) is
    #    caught by the value signal, and an explicit `pii_columns` override / datasource
    #    tag can always overrule for a stricter compliance posture.
    if tokens & _IDENTIFIER_TOKENS:
        return ColumnClass.IDENTIFIER
    # 3. Metric / time / status → safe.
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


def _value_signal(values: Sequence[object]) -> ColumnClass | None:
    """Classify from a column's sampled *values*, or ``None`` if inconclusive.

    Email values → PII (a direct identifier, even in a column *named* like a key —
    the natural-key-as-PII guard). UUID/hash-shaped, near-unique values look like a
    machine identifier; high-entropy encoded blobs are treated as sensitive. A ratio
    (not all-or-nothing) tolerates a few odd values in the sample."""
    cleaned = _clean_values(values)
    if not cleaned:
        return None
    n = len(cleaned)
    if sum(_looks_like_email(v) for v in cleaned) / n >= 0.5:
        return ColumnClass.PII
    id_shaped = sum(_looks_like_uuid(v) or _looks_like_hash(v) for v in cleaned)
    encoded = sum(_looks_encoded(v) for v in cleaned)
    distinct_ratio = len(set(cleaned)) / n

    if id_shaped / n >= 0.8 and distinct_ratio >= 0.8:
        return ColumnClass.IDENTIFIER
    if encoded / n >= 0.5:
        return ColumnClass.PII
    return None


def classify_column(name: str, sampled_values: Sequence[object] | None = None) -> ColumnClass:
    """Classify a column as IDENTIFIER / PII / SAFE for sample redaction (#415).

    Precedence:
    1. A **PII name** → PII (always mask).
    2. **Directly-sensitive values** (emails, encoded blobs) → PII — this *overrides* a
       name that looks like an identifier, so a natural key holding PII (a ``user_id``
       column of emails) is masked, not shown.
    3. The **name** signal (IDENTIFIER / SAFE) when it carried a known token.
    4. The **value** signal (IDENTIFIER) for a name-inconclusive column.
    5. Otherwise **PII** — conservative default-mask, so security never regresses.

    ``sampled_values`` are a small profile sample (a few rows); ``None``/empty falls
    back to the name signal only.
    """
    by_name = _name_signal(name)
    if by_name is ColumnClass.PII:
        return ColumnClass.PII
    by_value = _value_signal(sampled_values or [])
    if by_value is ColumnClass.PII:  # sensitive values override a name-based identifier
        return ColumnClass.PII
    if by_name is not None:  # IDENTIFIER or SAFE
        return by_name
    if by_value is not None:  # IDENTIFIER
        return by_value
    return ColumnClass.PII


def is_sensitive(name: str, sampled_values: Sequence[object] | None = None) -> bool:
    """Whether a column is **affirmatively** PII — a person/sensitive name token or a
    directly-sensitive value signal (emails, encoded blobs) — as opposed to the
    conservative *default* mask.

    This is the gate for a column that is otherwise shown (the **tested** column whose
    failing values are the point of the sample, or a designated **identifier**): show
    it *unless it is affirmatively sensitive*. Distinct from :func:`classify_column`,
    which default-masks an unrecognised column — appropriate for *incidental* columns,
    not for one the user deliberately checked or named.
    """
    return (
        _name_signal(name) is ColumnClass.PII
        or _value_signal(list(sampled_values or [])) is ColumnClass.PII
    )
