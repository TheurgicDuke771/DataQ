"""Comparison engine tests (ADR 0015, #793) — pure frames, no I/O.

Covers FDC bucket parity on identical-schema fixtures, the typed
duplicate-key/missing-column refusals, NULL semantics (null==null matches;
null-vs-value mismatches; a real "nan" string is NOT a null — the FDC
astype(str) quirk this port fixes), per-side key mapping, column
subset/intersection reconciliation, the mismatch-% scalar, sample caps, and
an adversarial pass (unicode keys, mixed dtypes across sides, empty frames).
"""

from typing import Any

import pandas as pd
import pytest

from backend.app.datasources.comparison import (
    ComparisonInputError,
    DuplicateKeyError,
    RecordComparisonResult,
    compare_records,
)


def _res(*, source: pd.DataFrame, target: pd.DataFrame, **kw: Any) -> RecordComparisonResult:
    kw.setdefault("keys", ["id"])
    return compare_records(source, target, **kw)


# ───────────────────────── bucket parity (FDC semantics) ────────────


def test_identical_frames_fully_match() -> None:
    df = pd.DataFrame({"id": [1, 2, 3], "amount": [10, 20, 30]})
    res = _res(source=df, target=df.copy())
    assert (res.matched, res.mismatched) == (3, 0)
    assert (res.additional_in_source, res.additional_in_target) == (0, 0)
    assert res.mismatch_percent == 0.0
    assert res.success


def test_buckets_split_like_fdc_outer_merge() -> None:
    source = pd.DataFrame({"id": [1, 2, 3], "amount": [10, 20, 30]})
    target = pd.DataFrame({"id": [2, 3, 4], "amount": [20, 31, 40]})
    res = _res(source=source, target=target)
    # id=2 matches, id=3 mismatches (30 vs 31), id=1 only-source, id=4 only-target.
    assert res.matched == 1
    assert res.mismatched == 1
    assert res.additional_in_source == 1
    assert res.additional_in_target == 1
    assert res.column_mismatch_counts == {"amount": 1}
    # union = 4 logical rows, 3 not-matched → 75%
    assert res.mismatch_percent == 75.0
    assert not res.success


def test_dtype_neutral_comparison_across_sides() -> None:
    # FDC's handling_datatypes parity: int 20 (numpy) vs "20" (string) vs
    # 20 (arrow-backed) all compare equal through the string cast.
    source = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    target = pd.DataFrame({"id": ["1", "2"], "amount": ["10", "20"]}).astype(
        {"id": "string", "amount": "string"}
    )
    res = _res(source=source, target=target)
    assert res.matched == 2 and res.success


def test_null_semantics_null_eq_null_but_not_value() -> None:
    source = pd.DataFrame({"id": [1, 2, 3], "v": [None, "x", None]})
    target = pd.DataFrame({"id": [1, 2, 3], "v": [None, "x", "y"]})
    res = _res(source=source, target=target)
    # id=1 null==null match, id=2 match, id=3 null-vs-value mismatch.
    assert res.matched == 2 and res.mismatched == 1


def test_literal_nan_string_is_not_null() -> None:
    # The FDC astype(str) quirk this port fixes: a genuine "nan" value must
    # mismatch a real NULL, not silently equal it.
    source = pd.DataFrame({"id": [1], "v": ["nan"]})
    target = pd.DataFrame({"id": [1], "v": [None]})
    res = _res(source=source, target=target)
    assert res.mismatched == 1


# ───────────────────────── keys ─────────────────────────────────────


def test_per_side_key_mapping() -> None:
    source = pd.DataFrame({"order_id": [1, 2], "v": ["a", "b"]})
    target = pd.DataFrame({"oid": [1, 2], "v": ["a", "b"]})
    res = _res(source=source, target=target, keys=[{"source": "order_id", "target": "oid"}])
    assert res.matched == 2 and res.success


def test_composite_keys() -> None:
    source = pd.DataFrame({"a": [1, 1], "b": ["x", "y"], "v": [1, 2]})
    target = pd.DataFrame({"a": [1, 1], "b": ["x", "y"], "v": [1, 9]})
    res = _res(source=source, target=target, keys=["a", "b"])
    assert res.matched == 1 and res.mismatched == 1


def test_duplicate_keys_refused_with_samples() -> None:
    dup = pd.DataFrame({"id": [1, 1, 2], "v": ["a", "b", "c"]})
    clean = pd.DataFrame({"id": [1, 2], "v": ["a", "c"]})
    with pytest.raises(DuplicateKeyError) as exc_info:
        _res(source=dup, target=clean)
    assert exc_info.value.detail["side"] == "source"
    assert {"id": "1"} in [
        {k: str(v) for k, v in s.items()} for s in exc_info.value.detail["sample_keys"]
    ]
    with pytest.raises(DuplicateKeyError):
        _res(source=clean, target=dup)


def test_missing_key_column_is_typed_error() -> None:
    source = pd.DataFrame({"id": [1], "v": ["a"]})
    target = pd.DataFrame({"other": [1], "v": ["a"]})
    with pytest.raises(ComparisonInputError, match="target side is missing"):
        _res(source=source, target=target)


def test_key_only_comparison_matches_on_presence() -> None:
    # No shared non-key columns → presence of the key IS the match.
    source = pd.DataFrame({"id": [1, 2], "src_extra": ["a", "b"]})
    target = pd.DataFrame({"id": [2, 3], "tgt_extra": ["b", "c"]})
    res = _res(source=source, target=target)
    assert res.matched == 1
    assert res.additional_in_source == 1 and res.additional_in_target == 1
    assert res.columns_compared == []
    assert res.columns_only_in_source == ["src_extra"]
    assert res.columns_only_in_target == ["tgt_extra"]


# ───────────────────────── column reconciliation ────────────────────


def test_explicit_columns_subset() -> None:
    source = pd.DataFrame({"id": [1], "a": ["x"], "b": ["y"]})
    target = pd.DataFrame({"id": [1], "a": ["x"], "b": ["DIFFERENT"]})
    res = _res(source=source, target=target, columns=["a"])
    assert res.matched == 1 and res.success  # b deliberately not compared


def test_explicit_columns_missing_on_a_side_is_typed_error() -> None:
    source = pd.DataFrame({"id": [1], "a": ["x"]})
    target = pd.DataFrame({"id": [1]})
    with pytest.raises(ComparisonInputError, match="target side is missing"):
        _res(source=source, target=target, columns=["a"])


def test_intersection_default_reports_extra_columns() -> None:
    source = pd.DataFrame({"id": [1], "common": ["x"], "only_src": [1]})
    target = pd.DataFrame({"id": [1], "common": ["x"], "only_tgt": [2]})
    res = _res(source=source, target=target)
    assert res.columns_compared == ["common"]
    assert res.columns_only_in_source == ["only_src"]
    assert res.columns_only_in_target == ["only_tgt"]
    assert res.success


# ───────────────────────── samples + scalar ─────────────────────────


def test_samples_are_capped_and_json_clean() -> None:
    n = 30
    source = pd.DataFrame({"id": range(n), "v": ["a"] * n})
    target = pd.DataFrame({"id": range(n), "v": [None] * n})
    res = _res(source=source, target=target, sample_limit=5)
    assert res.mismatched == n
    assert len(res.sample_mismatched) == 5
    row = res.sample_mismatched[0]
    assert row["v_src"] == "a" and row["v_tgt"] is None  # NA → None, JSON-clean


def test_empty_both_sides_is_vacuously_reconciled() -> None:
    empty = pd.DataFrame({"id": [], "v": []})
    res = _res(source=empty, target=empty.copy())
    assert res.mismatch_percent == 0.0 and res.success


def test_empty_source_all_rows_additional_in_target() -> None:
    source = pd.DataFrame({"id": [], "v": []})
    target = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    res = _res(source=source, target=target)
    assert res.additional_in_target == 2 and res.mismatch_percent == 100.0


# ───────────────────────── adversarial ──────────────────────────────


def test_unicode_and_whitespace_keys() -> None:
    source = pd.DataFrame({"id": ["ключ", " pad ", "emoji🙂"], "v": [1, 2, 3]})
    target = pd.DataFrame({"id": ["ключ", "pad", "emoji🙂"], "v": [1, 2, 3]})
    res = _res(source=source, target=target)
    # " pad " ≠ "pad" — whitespace is significant in keys.
    assert res.matched == 2
    assert res.additional_in_source == 1 and res.additional_in_target == 1


def test_arrow_backed_frames_compare_cleanly() -> None:
    source = pd.DataFrame({"id": [1, 2], "v": [10, None]}).convert_dtypes(dtype_backend="pyarrow")
    target = pd.DataFrame({"id": [1, 2], "v": [10, None]})
    res = _res(source=source, target=target)
    assert res.matched == 2 and res.success
