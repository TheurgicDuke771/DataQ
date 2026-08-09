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
* **An address/name token can be entity-qualified to SAFE** (#1182) — ``location_city``
  and ``carrier_name`` describe a *place*/label, not a person — but a co-occurring
  person-context token (``customer``/``delivery``/``shipping``/…) always overrides
  that back to PII, since the entity-qualifier alone is ambiguous.
* **Conservative default.** Anything not confidently IDENTIFIER or SAFE is PII, so the
  redactor's default-mask posture (security can't regress, #415) is preserved.

Pure, dependency-light, DB-free — unit-testable in isolation and reused by the
policy-derivation path (a later step wires it to auto-fill
``Suite.column_policy``).

**Full-population value signal (#1230):** the value signal is ratio-based, so it's
only as accurate as the population it sees. A capped sample (`gx_runner`'s
`SAMPLE_ROW_CAP`, #1196) is a much noisier estimator than the full failing
population it was capped from. `value_signal_summary` lets a capture-time writer
persist the exact counts those ratios need — computed over the full, pre-cap
population — as a small per-column summary; `classify_column`/`is_sensitive` accept
that summary and prefer it over re-deriving from the (now-capped) persisted rows.
See the comment above `_value_signal_counts` for the full mechanics.
"""

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


# ── Name-token vocabularies (matched against the column's word tokens) ──────────
# A person token → PII, unconditionally (no entity qualifier can un-flag these — an
# email/phone/ssn is direct PII no matter what noun it's attached to). Bare ``name``
# is handled specially (product_name is not a person), so it is NOT listed here; the
# person-name tokens below are explicit. Address-class tokens (city/street/zip/…) are
# NOT here either — see ``_ADDRESS_TOKENS``, which is entity-qualifiable (#1182).
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
# (``location_city``, ``warehouse_zip``, ``carrier_address``) describes a place, not a
# person, and over-flagging these dilutes the redaction signal (the original report:
# ``location_city`` on a logistics CSV). Default PII (keeps recall for the common case
# — a bare ``city``/``address``, or a person-qualified one like ``customer_city``), but
# SAFE when paired with a ``_NON_PERSON_ENTITIES`` qualifier — the same
# disambiguation already used for bare ``name`` below, applied here too.
_ADDRESS_TOKENS: frozenset[str] = frozenset(
    {"address", "street", "city", "zip", "zipcode", "postal", "postcode"}
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
# Tokens that mark the surrounding column as belonging to a SPECIFIC PERSON (a
# customer/recipient/etc.), not a generic entity (#1182 review finding). This wins
# over a ``_NON_PERSON_ENTITIES`` qualifier below: ``delivery_location_zip`` and
# ``customer_location_address`` both contain the ambiguous ``location`` qualifier,
# but the former names a facility/route while the latter is that customer's own
# address — conservative default (PII) must win on that ambiguity, so a co-occurring
# person-context token forces PII even in the presence of a non-person entity token.
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
# Entities that own a *non-person* ``name`` — product_name, category_name, … are labels,
# not PII. Also doubles (#1182) as the qualifier set for ``_ADDRESS_TOKENS`` above —
# ``location_city``/``warehouse_zip`` are a place, not a person's address. A
# co-occurring ``_PERSON_CONTEXT_TOKENS`` token always overrides this back to PII.
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
# Metric / time / status tokens — non-sensitive, SHOW when relevant. Includes
# opinion/label/logistics tokens whose bare form is not personal data (#1182:
# `rating`, `sentiment`, `carrier` were falling through to the conservative PII
# default with no entity link to a person at all).
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
    ``customerEmail`` → ``['customer', 'email']``). camelCase and snake/kebab both split."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _name_signal(name: str) -> ColumnClass | None:
    """Classify from the column *name* alone, or ``None`` if the name is inconclusive.

    Precedence: person-PII → address/name (entity-qualified, person-context-guarded)
    → sensitive-domain id (PII) → person-linking id (IDENTIFIER) → safe.
    """
    tokens = set(_tokens(name))
    if not tokens:
        return None

    # 1. Person / sensitive tokens, or an explicit person-name token → PII. Always —
    #    no entity qualifier un-flags an email/phone/ssn.
    if tokens & _PERSON_TOKENS or tokens & _PERSON_NAME_TOKENS:
        return ColumnClass.PII

    # A person-context qualifier (customer/delivery/shipping/…) always wins over a
    # non-person entity qualifier below (#1182 review finding): `delivery_location_zip`
    # and `customer_location_address` both carry the ambiguous `location` token, but
    # only the former is a facility/route — the latter is a specific person's address.
    # Conservative default (PII) wins whenever both are present.
    person_qualified = bool(tokens & _PERSON_CONTEXT_TOKENS)

    # 1b. Address-class tokens (#1182): PII by default (keeps recall — a bare `city`/
    #     `address`, or a person-qualified one like `customer_city`), but SAFE when
    #     paired with a non-person entity AND no person-context token is also present
    #     — `location_city`/`warehouse_zip` name a place, not a person's address.
    if tokens & _ADDRESS_TOKENS:
        if person_qualified:
            return ColumnClass.PII
        return ColumnClass.SAFE if tokens & _NON_PERSON_ENTITIES else ColumnClass.PII
    # Bare ``name``: PII by default (a person's name), but SAFE when it labels a
    # non-person entity (product_name / category_name are labels, not personal data) —
    # same person-context override as the address check above.
    if "name" in tokens:
        if person_qualified:
            return ColumnClass.PII
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


# ── Full-population value-signal summary (#1230) ────────────────────────────────
#
# `_value_signal`'s ratios (email ≥50%, id-shaped ≥80% + distinct-ratio ≥80%,
# encoded ≥50%) are only as good as the population they're computed over. Read-time
# redaction classifies each column from whatever values persisted alongside the
# result — and since #1196 capped every list-shaped sample key (incl.
# `unexpected_index_list`) to `SAMPLE_ROW_CAP` (20) *at capture time*, a column's
# full-population ratio (e.g. 60% email over thousands of rows) can no longer be
# reconstructed from the persisted rows alone for the pandas-backed datasources
# where that list used to arrive untruncated (flat-file / ADLS / S3 / Iceberg) — a
# 20-row window is a much noisier estimator, and can flip a genuinely-PII column to
# "not PII" or a genuinely-masked hash column to "identifier, show it".
#
# The fix: the *raw counts* behind those ratios are O(1) per column (not O(rows)),
# so a caller with the FULL, pre-cap value list — `gx_runner._extract_sample_failures`,
# before it truncates — can compute and persist them via `value_signal_summary`
# alongside the capped rows. `classify_column`/`is_sensitive` then prefer that
# summary (via `value_signal_summary=`) over re-deriving from the (now-capped)
# persisted rows, when a valid summary is present — restoring the full-population
# ratio without storing the full population itself. Old rows persisted before this
# existed carry no summary key, so they keep classifying from the capped rows they
# actually have (the only evidence there is), same as before.
_SUMMARY_COUNT_KEYS = ("n", "email_count", "id_shaped_count", "encoded_count", "distinct_count")


def _value_signal_counts(values: Iterable[object]) -> dict[str, int] | None:
    """The raw counts `_value_signal`'s ratios are computed from — total non-null
    values (``n``), plus how many looked like an email / id-shaped / encoded value,
    and the distinct count. ``None`` when there are no non-null values (mirrors
    `_value_signal`'s own empty-sample contract)."""
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
    """Public counts summary for persistence (#1230).

    Identical to `_value_signal_counts` — a public name because this is meant to be
    called from OUTSIDE this module, by a capture-time writer (`gx_runner`) that has
    the full, pre-cap value population and wants to persist the summary alongside
    the capped rows it stores. See the module note above `_SUMMARY_COUNT_KEYS` for
    why this exists. ``None`` when there are no non-null values to summarise (the
    caller should then simply omit the summary for that column).
    """
    return _value_signal_counts(values)


def _classify_counts(counts: Mapping[str, int]) -> ColumnClass | None:
    """`_value_signal`'s ratio logic, applied to pre-computed counts (either freshly
    derived from a sample, or a persisted `value_signal_summary`) rather than raw
    values directly — the shared tail both paths funnel through."""
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
    """Coerce/validate a persisted `value_signal_summary` sub-dict, or ``None`` if
    it's absent or malformed — an on-disk JSONB shape is never trusted blindly.
    Every count key must be present and coerce to an ``int``; ``n`` must be
    positive, no count may be negative (a zero/negative-population summary carries
    no signal — same as `_value_signal_counts`'s own ``None``-when-empty contract),
    and no sub-count may exceed ``n`` — a summary can't have more emails than total
    values. That cross-field check matters because `_classify_counts` divides each
    sub-count by ``n`` unguarded: an internally-inconsistent summary (corrupted
    JSONB, a future writer bug, a hand-edited row) with an inflated sub-count would
    otherwise pass the earlier checks and be trusted as real evidence, which could
    flip a genuinely-PII column to shown — precisely the regression this whole
    feature exists to prevent, reached through a bad summary instead of a capped
    window. A corrupt or hand-edited value can't be classified as if it were real
    evidence."""
    if not isinstance(summary, Mapping):
        return None
    try:
        counts = {key: int(summary[key]) for key in _SUMMARY_COUNT_KEYS}
    except (KeyError, TypeError, ValueError):
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
    """Classify from a column's sampled *values*, or ``None`` if inconclusive.

    Email values → PII (a direct identifier, even in a column *named* like a key —
    the natural-key-as-PII guard). UUID/hash-shaped, near-unique values look like a
    machine identifier; high-entropy encoded blobs are treated as sensitive. A ratio
    (not all-or-nothing) tolerates a few odd values in the sample.

    When a valid ``value_signal_summary`` (#1230) is given, its counts are preferred
    over re-deriving ratios from ``values`` — the summary was computed over the FULL
    pre-cap value population by the writer, so it is strictly more evidence than a
    capped sample can offer. Falls back to deriving from ``values`` when the summary
    is absent or fails validation (e.g. a result persisted before this existed).
    """
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
    back to the name signal only. ``value_signal_summary`` (#1230), when given and
    valid, is preferred over deriving the value signal from ``sampled_values`` — see
    `_value_signal`.
    """
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

    This is the gate for a column that is otherwise shown (the **tested** column whose
    failing values are the point of the sample, or a designated **identifier**): show
    it *unless it is affirmatively sensitive*. Distinct from :func:`classify_column`,
    which default-masks an unrecognised column — appropriate for *incidental* columns,
    not for one the user deliberately checked or named.

    ``value_signal_summary`` (#1230): see `_value_signal`.
    """
    return (
        _name_signal(name) is ColumnClass.PII
        or _value_signal(list(sampled_values or []), value_signal_summary=value_signal_summary)
        is ColumnClass.PII
    )
