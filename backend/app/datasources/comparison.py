"""Record-grain comparison engine (ADR 0015, #793) — the FDC diff port.

Ports the semantics of FastAPI_DataComparison (MIT, same author) —
`data_comparison/{record_comparison,column_comparison}.py` — as a pure frame
diff: no report files, no result cache, no connection store (ADR 0014 §2:
engine yes, app no). Given two already-bounded DataFrames (the #792
`DatasetReader` enforces the row cap) and the check's join keys:

* rows are keyed and outer-joined; key-only-in-source / key-only-in-target
  become the **additional_in_source / additional_in_target** buckets (FDC's
  ``left_only`` / ``right_only`` indicator semantics);
* keys present on both sides compare their non-key columns — all equal →
  **matched**, any difference → **mismatched**, with per-column mismatch
  counts (FDC's column-grain detail);
* values compare as **nullable strings** (FDC's ``handling_datatypes`` casts
  both frames to ``str`` for dtype-neutral comparison; this port uses pandas
  ``string`` dtype so real NULLs stay NULL instead of becoming the literal
  ``"nan"`` — null==null matches, null-vs-value mismatches);
* **duplicate join keys are a typed error** (never ambiguous buckets): FDC
  silently ``drop_duplicates``-ed whole rows, which under-counts — a key that
  appears twice on either side makes row pairing undefined, so the engine
  refuses with samples of the offending keys;
* the badness scalar is **mismatch-%** — non-matching rows over the union of
  logical rows — feeding `metric_value` + ADR 0016 severity banding.

Column reconciliation: explicit ``columns`` config wins; otherwise the
intersection of non-key columns is compared and each side's extra columns are
*reported* (``columns_only_in_*``), not an error — per-side SQL projections
make strict FDC same-columns enforcement too rigid, and schema drift is
visible in the result instead of hidden.

Batching latitude (ADR 0015 §3) is intentionally unused in this first build:
inputs are already capped by the reader, so a single vectorized merge is both
simplest and fastest at that scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json

# Cap on rows carried per sample bucket (→ `Result.sample_failures`, which is
# retention-swept and redacted downstream; samples must stay small).
SAMPLE_LIMIT = 20

# Cap on offending keys echoed in a duplicate-key error message.
_DUP_KEY_ECHO = 10


class ComparisonInputError(DataQError):
    """The frames/keys cannot be compared as configured (missing key column,
    nothing to compare) — an authoring/data-shape problem, not a data-quality
    failure. The run path maps it to an operational ``error`` result."""

    status_code = 422
    code = "comparison_input_invalid"


class DuplicateKeyError(DataQError):
    """Join keys are not unique on one side — row pairing would be undefined,
    so the diff refuses rather than produce ambiguous buckets."""

    status_code = 422
    code = "comparison_duplicate_keys"


@dataclass(frozen=True)
class KeyPair:
    """One join key, per-side names (identical unless the check mapped them)."""

    source: str
    target: str


@dataclass(frozen=True)
class RecordComparisonResult:
    """The diff outcome, shaped for `results` (ADR 0015 §4).

    ``mismatch_percent`` is the badness scalar for `metric_value`/banding:
    ``(mismatched + additional_in_source + additional_in_target) / union * 100``
    where ``union`` is the number of distinct logical rows across both sides
    (``matched + mismatched + additional_in_source + additional_in_target``).
    Two empty sides → 0.0 (vacuously reconciled).
    """

    source_rows: int
    target_rows: int
    matched: int
    mismatched: int
    additional_in_source: int
    additional_in_target: int
    mismatch_percent: float
    columns_compared: list[str] = field(default_factory=list)
    columns_only_in_source: list[str] = field(default_factory=list)
    columns_only_in_target: list[str] = field(default_factory=list)
    column_mismatch_counts: dict[str, int] = field(default_factory=dict)
    sample_mismatched: list[dict[str, Any]] = field(default_factory=list)
    sample_additional_in_source: list[dict[str, Any]] = field(default_factory=list)
    sample_additional_in_target: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """FDC parity: reconciled ⇔ every logical row matched exactly."""
        return (
            self.mismatched == 0
            and self.additional_in_source == 0
            and (self.additional_in_target == 0)
        )


def normalize_keys(keys: list[Any]) -> list[KeyPair]:
    """`config.keys` entries (validated at author time) → `KeyPair`s."""
    pairs: list[KeyPair] = []
    for key in keys:
        if isinstance(key, str):
            pairs.append(KeyPair(source=key, target=key))
        else:
            pairs.append(KeyPair(source=key["source"], target=key["target"]))
    return pairs


def _require_columns(df: Any, names: list[str], *, side: str) -> None:
    missing = [n for n in names if n not in df.columns]
    if missing:
        raise ComparisonInputError(
            f"{side} side is missing configured column(s): {', '.join(missing[:10])}",
            detail={"side": side, "missing": missing[:10]},
        )


def _reject_duplicate_keys(
    normalized: Any, original: Any, key_cols: list[str], *, side: str
) -> None:
    """Duplicate detection runs on the NORMALIZED keys (an int-1 and a "1" key
    are the same logical key), but the echoed samples come from the original
    frame so the user sees their own values, not the canonical form."""
    dup_mask = normalized.duplicated(subset=key_cols, keep=False)
    if bool(dup_mask.any()):
        dup_keys = (
            original.loc[dup_mask.to_numpy(), key_cols]
            .drop_duplicates()
            .head(_DUP_KEY_ECHO)
            .to_dict("records")
        )
        raise DuplicateKeyError(
            f"join keys are not unique on the {side} side — row pairing would be "
            "undefined, so the comparison refuses (dedupe upstream or add key columns)",
            detail={"side": side, "sample_keys": sanitize_json(dup_keys)},
        )


def _reject_null_keys(normalized: Any, key_cols: list[str], *, side: str) -> None:
    """NULL join keys are refused (SQL semantics: NULL joins nothing) — pandas'
    merge would silently pair NA keys across sides, welding two unrelated rows
    into a fabricated match/mismatch."""
    null_counts = {c: int(normalized[c].isna().sum()) for c in key_cols}
    nulls = {c: n for c, n in null_counts.items() if n}
    if nulls:
        raise ComparisonInputError(
            f"join key(s) contain NULLs on the {side} side — a NULL key cannot "
            "pair rows (filter them upstream or pick complete key columns)",
            detail={"side": side, "null_key_counts": nulls},
        )


def _is_datetime_like(s: Any) -> bool:
    import pandas as pd

    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if isinstance(s.dtype, pd.ArrowDtype):
        import pyarrow as pa

        pa_type = s.dtype.pyarrow_dtype
        return bool(pa.types.is_timestamp(pa_type) or pa.types.is_date(pa_type))
    return False


def _canonical_datetime_strings(s: Any) -> Any:
    """Render datetimes to one canonical form across backends.

    numpy `datetime64.astype(str)` is whole-column data-dependent (an
    all-midnight column renders date-only; one non-midnight value flips every
    element) and ArrowDtype timestamps render ISO-T with nanoseconds — so
    identical instants never string-match across the #792 readers. Canonical:
    tz-aware values are converted to UTC then rendered naive; microsecond
    precision (sub-µs is truncated — accepted, documented)."""
    s = (
        s.astype("datetime64[ns, UTC]")
        if getattr(s.dt, "tz", None) is not None
        else s.astype("datetime64[ns]")
    )
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.dt.strftime("%Y-%m-%dT%H:%M:%S.%f").astype("string")


def _normalize_pair(s: Any, t: Any) -> tuple[Any, Any]:
    """One column, both sides → comparable nullable-`string` series.

    The FDC dtype-neutralizer, null- and backend-safe:

    * real NULL stays `pd.NA` (FDC's plain ``astype(str)`` turned NaN into the
      literal ``"nan"``, which could match a genuine ``"nan"`` value);
    * numbers are canonicalized through nullable ``Float64`` when the pair is
      numeric-compatible — numpy coerces a NULL-carrying int column to float64
      (``10`` → ``"10.0"``) while Arrow keeps ``int64`` (``"10"``): same
      warehouse value, different string;
    * datetimes are canonicalized via `_canonical_datetime_strings` (numpy vs
      Arrow render identical instants differently, and numpy's rendering is
      data-dependent within a column);
    * a non-numeric/non-datetime side is adopted into the typed compare only
      when it parses **losslessly** (else both compare as plain strings).

    Accepted trades (documented): integers beyond float64's 2^53 exactness;
    float32-vs-float64 columns compare by exact value, so genuinely different
    stored precisions mismatch (numeric tolerance is a #799 follow-up); a
    tz-aware side compared against a naive side is normalized to UTC-naive.
    """
    import pandas as pd

    s_dt = _is_datetime_like(s)
    t_dt = _is_datetime_like(t)
    if s_dt or t_dt:
        s2 = s if s_dt else pd.to_datetime(s, errors="coerce", utc=False, format="mixed")
        t2 = t if t_dt else pd.to_datetime(t, errors="coerce", utc=False, format="mixed")
        s_lossless = s_dt or not bool((s2.isna() & s.notna()).any())
        t_lossless = t_dt or not bool((t2.isna() & t.notna()).any())
        if s_lossless and t_lossless:
            return _canonical_datetime_strings(s2), _canonical_datetime_strings(t2)
        return s.astype("string"), t.astype("string")

    s_num = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    t_num = pd.api.types.is_numeric_dtype(t) and not pd.api.types.is_bool_dtype(t)
    if s_num or t_num:
        s2 = s if s_num else pd.to_numeric(s, errors="coerce")
        t2 = t if t_num else pd.to_numeric(t, errors="coerce")
        s_lossless = s_num or not bool((s2.isna() & s.notna()).any())
        t_lossless = t_num or not bool((t2.isna() & t.notna()).any())
        if s_lossless and t_lossless:
            return s2.astype("Float64").astype("string"), t2.astype("Float64").astype("string")
    # Booleans compare as their string forms ("True"/"False") — deliberately
    # NOT via the numeric branch (is_numeric_dtype(bool) is True in pandas, but
    # canonicalizing True → "1.0" would mismatch a "True" string side).
    return s.astype("string"), t.astype("string")


def compare_records(
    source_df: Any,
    target_df: Any,
    *,
    keys: list[Any],
    columns: list[str] | None = None,
    sample_limit: int = SAMPLE_LIMIT,
) -> RecordComparisonResult:
    """Diff `target_df` (the suite's dataset under test) against `source_df`
    (the baseline) joined on `keys` (the check's `config.keys` shape).

    Raises `ComparisonInputError` / `DuplicateKeyError`; both map to
    operational ``error`` results in the run path (#794), never data-quality
    failures.
    """
    import pandas as pd

    key_pairs = normalize_keys(keys)
    src_keys = [p.source for p in key_pairs]
    tgt_keys = [p.target for p in key_pairs]
    _require_columns(source_df, src_keys, side="source")
    _require_columns(target_df, tgt_keys, side="target")

    # Rename target keys to the source names so the merge keys align (per-side
    # key mapping, ADR 0015 §1). Non-key columns keep their own names — the
    # compared set is resolved by name below.
    rename_map = {p.target: p.source for p in key_pairs if p.target != p.source}
    collisions = [dst for dst in rename_map.values() if dst in target_df.columns]
    if collisions:
        # Renaming onto an existing label would create duplicate columns and
        # crash deep in normalization — refuse with the actionable cause.
        raise ComparisonInputError(
            "target side already has column(s) named like the mapped source "
            f"key(s): {', '.join(collisions[:10])} — rename or drop them",
            detail={"side": "target", "collisions": collisions[:10]},
        )
    target_df = target_df.rename(columns=rename_map)
    key_cols = src_keys

    source_value_cols = [c for c in source_df.columns if c not in key_cols]
    target_value_cols = [c for c in target_df.columns if c not in key_cols]
    if columns:
        _require_columns(source_df, columns, side="source")
        _require_columns(target_df, columns, side="target")
        compared = [c for c in columns if c not in key_cols]
    else:
        compared = [c for c in source_value_cols if c in set(target_value_cols)]
    only_source = sorted(set(source_value_cols) - set(compared) - set(key_cols))
    only_target = sorted(set(target_value_cols) - set(compared) - set(key_cols))

    # Normalize each (key + compared) column PAIRWISE so cross-backend dtype
    # skew can't fabricate mismatches, then dedup-check and merge on the
    # normalized keys (an int-1 and a "1" key are the same logical key).
    src = pd.DataFrame(index=source_df.index)
    tgt = pd.DataFrame(index=target_df.index)
    for col in key_cols + compared:
        src[col], tgt[col] = _normalize_pair(source_df[col], target_df[col])

    _reject_null_keys(src, key_cols, side="source")
    _reject_null_keys(tgt, key_cols, side="target")
    _reject_duplicate_keys(src, source_df, key_cols, side="source")
    _reject_duplicate_keys(tgt, target_df, key_cols, side="target")

    merged = src.merge(tgt, on=key_cols, how="outer", suffixes=("_src", "_tgt"), indicator=True)
    additional_src = merged[merged["_merge"] == "left_only"]
    additional_tgt = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"]

    # Row-wise equality across compared columns, NULL==NULL counted as equal.
    column_mismatch_counts: dict[str, int] = {}
    if compared:
        neq_frame = pd.DataFrame(index=both.index)
        for col in compared:
            s, t = both[f"{col}_src"], both[f"{col}_tgt"]
            # NA-safe: `string`-dtype eq() yields NA (not False) when exactly
            # one side is NULL — fillna(False) so null-vs-value counts as a
            # mismatch instead of silently masking as a match.
            eq = s.eq(t).fillna(False) | (s.isna() & t.isna())
            neq = ~eq
            neq_frame[col] = neq
            count = int(neq.sum())
            if count:
                column_mismatch_counts[col] = count
        mismatch_mask = neq_frame.any(axis=1)
    else:
        # Key-only comparison: presence of the key on both sides IS the match.
        mismatch_mask = pd.Series(False, index=both.index)
    mismatched_rows = both[mismatch_mask]

    matched = int(len(both) - len(mismatched_rows))
    mismatched = len(mismatched_rows)
    add_src = len(additional_src)
    add_tgt = len(additional_tgt)
    union = matched + mismatched + add_src + add_tgt
    badness = ((mismatched + add_src + add_tgt) / union * 100.0) if union else 0.0

    def _sample(frame: Any, cols: list[str]) -> list[dict[str, Any]]:
        head = frame[cols].head(sample_limit)
        # `string` dtype NA → None so samples are JSON-clean.
        return [
            {k: (None if pd.isna(v) else v) for k, v in row.items()}
            for row in head.to_dict("records")
        ]

    mismatch_cols = key_cols + [f"{c}_{side}" for c in compared for side in ("src", "tgt")]
    src_only_cols = key_cols + [f"{c}_src" for c in compared]
    tgt_only_cols = key_cols + [f"{c}_tgt" for c in compared]

    return RecordComparisonResult(
        source_rows=len(source_df),
        target_rows=len(target_df),
        matched=matched,
        mismatched=mismatched,
        additional_in_source=add_src,
        additional_in_target=add_tgt,
        mismatch_percent=round(badness, 4),
        columns_compared=compared,
        columns_only_in_source=only_source,
        columns_only_in_target=only_target,
        column_mismatch_counts=column_mismatch_counts,
        sample_mismatched=_sample(mismatched_rows, mismatch_cols),
        sample_additional_in_source=_sample(additional_src, src_only_cols),
        sample_additional_in_target=_sample(additional_tgt, tgt_only_cols),
    )
