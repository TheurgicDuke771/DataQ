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

Two grains share one alignment pass (#799): `compare_records` (row grain,
above) and `compare_columns` (FDC's column grain — per-column mismatched /
additional-per-side value buckets; a value null on the only side it exists on
counts nowhere, FDC's ``dropna`` parity). The alignment merges **keys +
row positions only** and gathers value columns for just the paired rows —
FDC (and the first #793 port) pushed every value column through the outer
merge's suffix machinery, copying the whole frame twice; see the benchmark
note on the #799 PR.

Numeric **tolerance** (#799, from the #808 review): an optional
``{"absolute": a, "relative": r}`` config treats a numeric pair as equal when
``|s - t| <= max(a, r · max(|s|, |t|))`` — float32-vs-float64 round-trips and
engine-precision skew stop mismatching. Applies only to columns whose pair
normalized numerically; one-sided NULLs still mismatch.
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


@dataclass(frozen=True)
class Tolerance:
    """Numeric closeness for tolerance-aware equality (#799): equal when
    ``|s - t| <= max(absolute, relative · max(|s|, |t|))``."""

    absolute: float = 0.0
    relative: float = 0.0


def parse_tolerance(raw: Any) -> Tolerance | None:
    """`config.tolerance` → `Tolerance`; None when absent. Raises
    `ComparisonInputError` on a malformed shape (author-time validation
    mirrors this, so at run time it is defence in depth)."""
    if raw is None:
        return None
    if (
        not isinstance(raw, dict)
        or not raw
        or not set(raw) <= {"absolute", "relative"}
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in raw.values()
        )
    ):
        raise ComparisonInputError(
            'config.tolerance must be {"absolute"?: number>=0, "relative"?: number>=0} '
            "with at least one bound",
            detail={"field": "config.tolerance"},
        )
    return Tolerance(
        absolute=float(raw.get("absolute", 0.0)), relative=float(raw.get("relative", 0.0))
    )


@dataclass(frozen=True)
class ColumnComparisonResult:
    """The column-grain outcome (#799 — FDC `column_comparison` parity).

    Counts are **value slots**, not rows: per compared column, a paired row
    contributes `matched` (equal, incl. both-null) or `mismatched` (both
    present, different); a value present on only one side (key missing or
    NULL opposite) is `additional_in_<side>`; a null value on its only side
    counts nowhere (FDC ``dropna`` parity). ``mismatch_percent`` = non-matched
    slots over all counted slots (0.0 when nothing was comparable).
    """

    source_rows: int
    target_rows: int
    matched_values: int
    mismatched_values: int
    additional_in_source_values: int
    additional_in_target_values: int
    mismatch_percent: float
    columns_compared: list[str] = field(default_factory=list)
    columns_only_in_source: list[str] = field(default_factory=list)
    columns_only_in_target: list[str] = field(default_factory=list)
    per_column: dict[str, dict[str, int]] = field(default_factory=dict)
    sample_mismatched: list[dict[str, Any]] = field(default_factory=list)
    sample_additional_in_source: list[dict[str, Any]] = field(default_factory=list)
    sample_additional_in_target: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Reconciled ⇔ every comparable value slot matched."""
        return (
            self.mismatched_values == 0
            and self.additional_in_source_values == 0
            and self.additional_in_target_values == 0
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


@dataclass(frozen=True)
class _NormalizedPair:
    """One column normalized for comparison: the canonical `string` forms, plus
    the nullable-`Float64` pair when the column normalized numerically (the
    tolerance-aware equality operand, #799)."""

    source_str: Any
    target_str: Any
    source_num: Any | None = None
    target_num: Any | None = None


def _normalize_pair(s: Any, t: Any) -> _NormalizedPair:
    """One column, both sides → comparable nullable-`string` series (plus the
    numeric pair when applicable — see `_NormalizedPair`).

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
    float32-vs-float64 columns compare by exact value unless the check sets a
    numeric `tolerance` (#799); a tz-aware side compared against a naive side
    is normalized to UTC-naive.
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
            return _NormalizedPair(_canonical_datetime_strings(s2), _canonical_datetime_strings(t2))
        return _NormalizedPair(s.astype("string"), t.astype("string"))

    s_num = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    t_num = pd.api.types.is_numeric_dtype(t) and not pd.api.types.is_bool_dtype(t)
    if s_num or t_num:
        s2 = s if s_num else pd.to_numeric(s, errors="coerce")
        t2 = t if t_num else pd.to_numeric(t, errors="coerce")
        s_lossless = s_num or not bool((s2.isna() & s.notna()).any())
        t_lossless = t_num or not bool((t2.isna() & t.notna()).any())
        if s_lossless and t_lossless:
            # Integer-only pairs canonicalize through Int64 so keys/values render
            # "1", not "1.0" (samples echo these); any float involvement → Float64
            # (the cross-backend NULL-int→float64 skew still lands here and still
            # matches, since BOTH sides then render "10.0").
            if pd.api.types.is_integer_dtype(s2) and pd.api.types.is_integer_dtype(t2):
                try:
                    s_f, t_f = s2.astype("Int64"), t2.astype("Int64")
                except (TypeError, OverflowError):
                    # uint64 above int64-max can't cast safely — fall back to the
                    # Float64 canonical form (main's behavior) instead of erroring
                    # a perfectly comparable column.
                    s_f, t_f = s2.astype("Float64"), t2.astype("Float64")
            else:
                s_f, t_f = s2.astype("Float64"), t2.astype("Float64")
            # The numeric pair rides along for tolerance-aware equality (#799).
            return _NormalizedPair(
                s_f.astype("string"), t_f.astype("string"), source_num=s_f, target_num=t_f
            )
    # Booleans compare as their string forms ("True"/"False") — deliberately
    # NOT via the numeric branch (is_numeric_dtype(bool) is True in pandas, but
    # canonicalizing True → "1.0" would mismatch a "True" string side).
    return _NormalizedPair(s.astype("string"), t.astype("string"))


@dataclass(frozen=True)
class _AlignedSides:
    """One shared alignment pass for both grains (#799).

    The merge carries **keys + row positions only** — value columns are
    gathered afterwards for exactly the rows each bucket needs, instead of
    copying every value column through the outer merge's suffix machinery
    (the FDC / first-port shape). `pairs` maps each compared column to its
    normalized forms; positions are positional (both frames are reset to a
    RangeIndex before merging).
    """

    key_cols: list[str]
    compared: list[str]
    only_source_cols: list[str]
    only_target_cols: list[str]
    pairs: dict[str, _NormalizedPair]  # keys + compared
    both_src: Any  # np.ndarray of source positions paired with both_tgt
    both_tgt: Any
    only_src: Any  # np.ndarray — source rows with no target key
    only_tgt: Any
    source_rows: int
    target_rows: int


def _align(
    source_df: Any,
    target_df: Any,
    *,
    keys: list[Any],
    columns: list[str] | None,
) -> _AlignedSides:
    """Validate, normalize, and key-align the two sides (shared by both grains).

    Raises `ComparisonInputError` / `DuplicateKeyError` — the run path maps
    both to operational ``error`` results (#122), never data-quality failures.
    """
    import numpy as np
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

    # Name-collision guards (typed refusals, never silent wrong output):
    # the alignment reserves `__dataq_pos*` in its keys+positions merge, and
    # samples key compared columns as `<col>_src`/`<col>_tgt` beside the raw
    # key names — a user column landing on either would overwrite silently.
    reserved = {"__dataq_pos", "__dataq_pos_src", "__dataq_pos_tgt"} & set(key_cols + compared)
    if reserved:
        raise ComparisonInputError(
            f"column name(s) reserved by the comparison engine: {', '.join(sorted(reserved))} "
            "— rename or exclude them",
            detail={"reserved": sorted(reserved)},
        )
    suffixed = {f"{c}_{side}" for c in compared for side in ("src", "tgt")}
    shadowed = sorted(suffixed & set(key_cols))
    if shadowed:
        raise ComparisonInputError(
            "key column name(s) collide with a compared column's sample suffix "
            f"({', '.join(shadowed)}) — sample rows would overwrite the key; rename "
            "the key or exclude the column via config.columns",
            detail={"collisions": shadowed},
        )

    # Positional frames: every later gather is an .iloc on 0..n-1.
    source_df = source_df.reset_index(drop=True)
    target_df = target_df.reset_index(drop=True)

    # Normalize each (key + compared) column PAIRWISE so cross-backend dtype
    # skew can't fabricate mismatches; dedup/NULL-check on the NORMALIZED keys
    # (an int-1 and a "1" key are the same logical key).
    pairs = {col: _normalize_pair(source_df[col], target_df[col]) for col in key_cols + compared}
    src_key_frame = pd.DataFrame({c: pairs[c].source_str for c in key_cols})
    tgt_key_frame = pd.DataFrame({c: pairs[c].target_str for c in key_cols})

    _reject_null_keys(src_key_frame, key_cols, side="source")
    _reject_null_keys(tgt_key_frame, key_cols, side="target")
    _reject_duplicate_keys(src_key_frame, source_df, key_cols, side="source")
    _reject_duplicate_keys(tgt_key_frame, target_df, key_cols, side="target")

    # Keys + positions only through the merge (#799 optimization) — value
    # columns never ride the suffix machinery.
    left = src_key_frame.assign(__dataq_pos=np.arange(len(src_key_frame)))
    right = tgt_key_frame.assign(__dataq_pos=np.arange(len(tgt_key_frame)))
    merged = left.merge(right, on=key_cols, how="outer", suffixes=("_src", "_tgt"), indicator=True)
    both = merged[merged["_merge"] == "both"]
    return _AlignedSides(
        key_cols=key_cols,
        compared=compared,
        only_source_cols=only_source,
        only_target_cols=only_target,
        pairs=pairs,
        both_src=both["__dataq_pos_src"].to_numpy(dtype="int64"),
        both_tgt=both["__dataq_pos_tgt"].to_numpy(dtype="int64"),
        only_src=merged.loc[merged["_merge"] == "left_only", "__dataq_pos_src"].to_numpy(
            dtype="int64"
        ),
        only_tgt=merged.loc[merged["_merge"] == "right_only", "__dataq_pos_tgt"].to_numpy(
            dtype="int64"
        ),
        source_rows=len(source_df),
        target_rows=len(target_df),
    )


def _gather(series: Any, positions: Any) -> Any:
    return series.iloc[positions].reset_index(drop=True)


def _pair_equality(
    aligned: _AlignedSides, col: str, tolerance: Tolerance | None
) -> tuple[Any, Any, Any]:
    """(eq, s, t) for `col` over the paired rows — NA-safe, tolerance-aware.

    `string`-dtype ``eq()`` yields NA (not False) when exactly one side is
    NULL — ``fillna(False)`` so null-vs-value counts as a difference instead
    of silently masking as a match. With a `tolerance` and a numeric pair,
    closeness also counts as equal (one-sided NULLs still differ).
    """
    import numpy as np
    import pandas as pd

    pair = aligned.pairs[col]
    s = _gather(pair.source_str, aligned.both_src)
    t = _gather(pair.target_str, aligned.both_tgt)
    eq = s.eq(t).fillna(False) | (s.isna() & t.isna())
    if tolerance is not None and pair.source_num is not None and len(s):
        # numpy formulation: NA → NaN, and every NaN comparison is False, so a
        # one-sided NULL can never become tolerance-equal.
        a = _gather(pair.source_num, aligned.both_src).to_numpy(dtype="float64", na_value=np.nan)
        b = _gather(pair.target_num, aligned.both_tgt).to_numpy(dtype="float64", na_value=np.nan)
        allowed = np.maximum(
            tolerance.absolute, tolerance.relative * np.maximum(np.abs(a), np.abs(b))
        )
        close = np.abs(a - b) <= allowed
        eq = eq | pd.Series(close, index=eq.index)
    return eq, s, t


def _sample_rows(frames: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """First `limit` rows of aligned same-length series → JSON-clean dicts."""
    import pandas as pd

    count = min(limit, len(next(iter(frames.values()))) if frames else 0)
    out: list[dict[str, Any]] = []
    for i in range(count):
        out.append(
            {
                name: (None if pd.isna(v) else v)
                for name, v in ((name, series.iloc[i]) for name, series in frames.items())
            }
        )
    return out


def compare_records(
    source_df: Any,
    target_df: Any,
    *,
    keys: list[Any],
    columns: list[str] | None = None,
    tolerance: Tolerance | None = None,
    sample_limit: int = SAMPLE_LIMIT,
) -> RecordComparisonResult:
    """Row-grain diff of `target_df` (the suite's dataset under test) against
    `source_df` (the baseline), joined on `keys` (the check's `config.keys`
    shape). See the module docstring for bucket semantics.
    """
    import numpy as np

    aligned = _align(source_df, target_df, keys=keys, columns=columns)
    both_n = len(aligned.both_src)

    column_mismatch_counts: dict[str, int] = {}
    if aligned.compared and both_n:
        neq_any = np.zeros(both_n, dtype=bool)
        for col in aligned.compared:
            eq, _, _ = _pair_equality(aligned, col, tolerance)
            neq = (~eq).to_numpy(dtype=bool)
            count = int(neq.sum())
            if count:
                column_mismatch_counts[col] = count
            neq_any |= neq
    else:
        # Key-only comparison: presence of the key on both sides IS the match.
        neq_any = np.zeros(both_n, dtype=bool)
    mismatch_pos = np.flatnonzero(neq_any)

    matched = int(both_n - len(mismatch_pos))
    mismatched = len(mismatch_pos)
    add_src = len(aligned.only_src)
    add_tgt = len(aligned.only_tgt)
    union = matched + mismatched + add_src + add_tgt
    badness = ((mismatched + add_src + add_tgt) / union * 100.0) if union else 0.0

    def _side_frames(positions: Any, *, side: str) -> dict[str, Any]:
        attr = "source_str" if side == "src" else "target_str"
        frames = {c: _gather(getattr(aligned.pairs[c], attr), positions) for c in aligned.key_cols}
        for c in aligned.compared:
            frames[f"{c}_{side}"] = _gather(getattr(aligned.pairs[c], attr), positions)
        return frames

    mismatch_src_pos = aligned.both_src[mismatch_pos][:sample_limit]
    mismatch_tgt_pos = aligned.both_tgt[mismatch_pos][:sample_limit]
    mismatch_frames = {
        c: _gather(aligned.pairs[c].source_str, mismatch_src_pos) for c in aligned.key_cols
    }
    for c in aligned.compared:
        mismatch_frames[f"{c}_src"] = _gather(aligned.pairs[c].source_str, mismatch_src_pos)
        mismatch_frames[f"{c}_tgt"] = _gather(aligned.pairs[c].target_str, mismatch_tgt_pos)

    return RecordComparisonResult(
        source_rows=aligned.source_rows,
        target_rows=aligned.target_rows,
        matched=matched,
        mismatched=mismatched,
        additional_in_source=add_src,
        additional_in_target=add_tgt,
        mismatch_percent=round(badness, 4),
        columns_compared=aligned.compared,
        columns_only_in_source=aligned.only_source_cols,
        columns_only_in_target=aligned.only_target_cols,
        column_mismatch_counts=column_mismatch_counts,
        sample_mismatched=_sample_rows(mismatch_frames, sample_limit),
        sample_additional_in_source=_sample_rows(
            _side_frames(aligned.only_src[:sample_limit], side="src"), sample_limit
        ),
        sample_additional_in_target=_sample_rows(
            _side_frames(aligned.only_tgt[:sample_limit], side="tgt"), sample_limit
        ),
    )


def compare_columns(
    source_df: Any,
    target_df: Any,
    *,
    keys: list[Any],
    columns: list[str] | None = None,
    tolerance: Tolerance | None = None,
    sample_limit: int = SAMPLE_LIMIT,
) -> ColumnComparisonResult:
    """Column-grain diff (#799 — FDC `column_comparison` parity): per compared
    column, count matched / mismatched / additional-per-side **value slots**.

    FDC parity notes: a value present on one side while the other side's value
    is NULL — or the key row is absent entirely — is `additional_in_<side>`
    for that column (FDC's outer-join ``mismatch()`` split); a NULL value on
    its only side counts nowhere (FDC's ``dropna``). Samples are shaped like
    the record grain (`<col>_src` / `<col>_tgt` keys), so redaction and the
    report writer treat both grains identically.
    """
    aligned = _align(source_df, target_df, keys=keys, columns=columns)
    if not aligned.compared:
        raise ComparisonInputError(
            "column-grain comparison needs at least one shared non-key column "
            "(or an explicit config.columns list)",
            detail={"columns_only_in_source": aligned.only_source_cols[:10]},
        )

    per_column: dict[str, dict[str, int]] = {}
    totals = {"matched": 0, "mismatched": 0, "additional_in_source": 0, "additional_in_target": 0}
    samples_mismatched: list[dict[str, Any]] = []
    samples_add_src: list[dict[str, Any]] = []
    samples_add_tgt: list[dict[str, Any]] = []

    def _keyed(positions: Any, *, side: str, col: str, values: dict[str, Any]) -> None:
        """Append sample rows: the key columns (from `side`) + `values`."""
        attr = "source_str" if side == "src" else "target_str"
        frames = {c: _gather(getattr(aligned.pairs[c], attr), positions) for c in aligned.key_cols}
        frames.update(values)
        target_list = {
            "mismatched": samples_mismatched,
            "additional_in_source": samples_add_src,
            "additional_in_target": samples_add_tgt,
        }[col]
        target_list.extend(_sample_rows(frames, sample_limit - len(target_list)))

    for col in aligned.compared:
        pair = aligned.pairs[col]
        eq, s, t = _pair_equality(aligned, col, tolerance)
        s_has, t_has = s.notna(), t.notna()
        mism_mask = (s_has & t_has & ~eq).to_numpy(dtype=bool)
        # One-sided within paired rows + rows missing entirely on the other side
        # (value must be non-null on its own side — FDC dropna parity).
        add_src_paired = (s_has & ~t_has).to_numpy(dtype=bool)
        add_tgt_paired = (t_has & ~s_has).to_numpy(dtype=bool)
        only_src_vals = _gather(pair.source_str, aligned.only_src)
        only_tgt_vals = _gather(pair.target_str, aligned.only_tgt)
        import numpy as np

        mismatched = int(mism_mask.sum())
        add_src = int(add_src_paired.sum()) + int(only_src_vals.notna().sum())
        add_tgt = int(add_tgt_paired.sum()) + int(only_tgt_vals.notna().sum())
        matched = int((eq.to_numpy(dtype=bool)).sum())
        per_column[col] = {
            "matched": matched,
            "mismatched": mismatched,
            "additional_in_source": add_src,
            "additional_in_target": add_tgt,
        }
        for bucket, count in per_column[col].items():
            if bucket in totals:
                totals[bucket] += count

        if mismatched and len(samples_mismatched) < sample_limit:
            pos = np.flatnonzero(mism_mask)[: sample_limit - len(samples_mismatched)]
            _keyed(
                aligned.both_src[pos],
                side="src",
                col="mismatched",
                values={
                    f"{col}_src": _gather(s, pos),
                    f"{col}_tgt": _gather(t, pos),
                },
            )
        if len(samples_add_src) < sample_limit and (add_src_paired.any() or len(aligned.only_src)):
            paired_pos = np.flatnonzero(add_src_paired)[: sample_limit - len(samples_add_src)]
            if len(paired_pos):
                _keyed(
                    aligned.both_src[paired_pos],
                    side="src",
                    col="additional_in_source",
                    values={f"{col}_src": _gather(s, paired_pos)},
                )
            only_pos_mask = only_src_vals.notna().to_numpy(dtype=bool)
            only_pos = np.flatnonzero(only_pos_mask)[: max(0, sample_limit - len(samples_add_src))]
            if len(only_pos):
                _keyed(
                    aligned.only_src[only_pos],
                    side="src",
                    col="additional_in_source",
                    values={f"{col}_src": _gather(only_src_vals, only_pos)},
                )
        if len(samples_add_tgt) < sample_limit and (add_tgt_paired.any() or len(aligned.only_tgt)):
            paired_pos = np.flatnonzero(add_tgt_paired)[: sample_limit - len(samples_add_tgt)]
            if len(paired_pos):
                _keyed(
                    aligned.both_tgt[paired_pos],
                    side="tgt",
                    col="additional_in_target",
                    values={f"{col}_tgt": _gather(t, paired_pos)},
                )
            only_pos_mask = only_tgt_vals.notna().to_numpy(dtype=bool)
            only_pos = np.flatnonzero(only_pos_mask)[: max(0, sample_limit - len(samples_add_tgt))]
            if len(only_pos):
                _keyed(
                    aligned.only_tgt[only_pos],
                    side="tgt",
                    col="additional_in_target",
                    values={f"{col}_tgt": _gather(only_tgt_vals, only_pos)},
                )

    slots = sum(totals.values())
    non_matched = (
        totals["mismatched"] + totals["additional_in_source"] + totals["additional_in_target"]
    )
    badness = (non_matched / slots * 100.0) if slots else 0.0

    return ColumnComparisonResult(
        source_rows=aligned.source_rows,
        target_rows=aligned.target_rows,
        matched_values=totals["matched"],
        mismatched_values=totals["mismatched"],
        additional_in_source_values=totals["additional_in_source"],
        additional_in_target_values=totals["additional_in_target"],
        mismatch_percent=round(badness, 4),
        columns_compared=aligned.compared,
        columns_only_in_source=aligned.only_source_cols,
        columns_only_in_target=aligned.only_target_cols,
        per_column=per_column,
        sample_mismatched=samples_mismatched,
        sample_additional_in_source=samples_add_src,
        sample_additional_in_target=samples_add_tgt,
    )
