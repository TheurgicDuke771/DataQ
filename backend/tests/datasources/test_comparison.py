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


def test_datetimes_match_across_numpy_and_arrow_backends() -> None:
    # Identical instants: numpy datetime64 renders "2026-01-01 10:00:00" while
    # arrow timestamps render ISO-T with nanoseconds — the canonicalizer must
    # make them equal (this exact skew comes from the #792 readers).
    ts = ["2026-01-01T10:00:00", "2026-01-02T00:00:00"]
    source = pd.DataFrame({"id": [1, 2], "ts": pd.to_datetime(ts)})
    target = pd.DataFrame(
        {"id": [1, 2], "ts": pd.array(pd.to_datetime(ts), dtype="timestamp[ns][pyarrow]")}
    )
    res = _res(source=source, target=target)
    assert res.matched == 2 and res.success


def test_datetime_rendering_not_data_dependent_within_column() -> None:
    # numpy astype(str) renders an all-midnight column date-only, but flips to
    # full timestamps once any row has a time component — two equal rows must
    # not mismatch because an unrelated third row exists on one side.
    source = pd.DataFrame({"id": [1, 2], "ts": pd.to_datetime(["2026-01-01", "2026-01-02"])})
    target = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "ts": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03 05:00:00"], format="mixed"
            ),
        }
    )
    res = _res(source=source, target=target)
    assert res.matched == 2 and res.mismatched == 0
    assert res.additional_in_target == 1


def test_tz_aware_side_normalizes_to_utc() -> None:
    aware = pd.to_datetime(["2026-01-01 12:00:00+02:00"])
    naive_utc = pd.to_datetime(["2026-01-01 10:00:00"])
    res = _res(
        source=pd.DataFrame({"id": [1], "ts": aware}),
        target=pd.DataFrame({"id": [1], "ts": naive_utc}),
    )
    assert res.matched == 1


def test_null_join_keys_refused() -> None:
    source = pd.DataFrame({"id": [None, "1"], "v": ["a", "b"]})
    target = pd.DataFrame({"id": ["1"], "v": ["b"]})
    with pytest.raises(ComparisonInputError, match="NULL"):
        _res(source=source, target=target)


def test_key_mapping_collision_is_typed_error() -> None:
    # target already has 'order_id'; mapping oid→order_id would create
    # duplicate labels and crash unhandled without the guard.
    source = pd.DataFrame({"order_id": [1], "v": ["a"]})
    target = pd.DataFrame({"oid": [1], "order_id": [9], "v": ["a"]})
    with pytest.raises(ComparisonInputError, match="order_id"):
        _res(source=source, target=target, keys=[{"source": "order_id", "target": "oid"}])


def test_duplicate_key_detail_is_json_serializable() -> None:
    import json

    dup = pd.DataFrame({"id": [1, 1], "v": ["a", "b"]})
    clean = pd.DataFrame({"id": [1], "v": ["a"]})
    with pytest.raises(DuplicateKeyError) as exc_info:
        _res(source=dup, target=clean)
    json.dumps(exc_info.value.detail)  # np.int64 keys must be sanitized


def test_bools_do_not_canonicalize_numerically() -> None:
    # is_numeric_dtype(bool) is True in pandas — without the bool carve-out,
    # True would render "1.0" and mismatch a "True" string side.
    source = pd.DataFrame({"id": [1], "flag": [True]})
    target = pd.DataFrame({"id": [1], "flag": ["True"]})
    res = _res(source=source, target=target)
    assert res.matched == 1


def test_arrow_backed_frames_compare_cleanly() -> None:
    source = pd.DataFrame({"id": [1, 2], "v": [10, None]}).convert_dtypes(dtype_backend="pyarrow")
    target = pd.DataFrame({"id": [1, 2], "v": [10, None]})
    res = _res(source=source, target=target)
    assert res.matched == 2 and res.success


# ───────────────────────── tolerance (#799) ─────────────────────────


def test_tolerance_absolute_and_relative() -> None:
    from backend.app.datasources.comparison import Tolerance

    source = pd.DataFrame({"id": [1, 2, 3], "v": [100.0, 200.0, 300.0]})
    target = pd.DataFrame({"id": [1, 2, 3], "v": [100.4, 210.0, 300.0]})
    exact = _res(source=source, target=target)
    assert exact.mismatched == 2
    absolute = _res(source=source, target=target, tolerance=Tolerance(absolute=0.5))
    assert absolute.mismatched == 1  # 100.4 within 0.5; 210 not
    relative = _res(source=source, target=target, tolerance=Tolerance(relative=0.05))
    assert relative.mismatched == 0  # both within 5%


def test_tolerance_never_equates_one_sided_null_or_strings() -> None:
    from backend.app.datasources.comparison import Tolerance

    source = pd.DataFrame({"id": [1, 2], "v": [1.0, None], "s": ["a", "b"]})
    target = pd.DataFrame({"id": [1, 2], "v": [1.0, 2.0], "s": ["a", "c"]})
    res = _res(source=source, target=target, tolerance=Tolerance(absolute=1e9))
    # The huge tolerance cannot rescue a NULL-vs-value or a string mismatch.
    assert res.mismatched == 1
    assert res.column_mismatch_counts == {"v": 1, "s": 1}


def test_parse_tolerance_validation() -> None:
    from backend.app.datasources.comparison import parse_tolerance

    assert parse_tolerance(None) is None
    tol = parse_tolerance({"absolute": 0.5, "relative": 0.01})
    assert tol is not None and tol.absolute == 0.5 and tol.relative == 0.01
    for bad in [{}, {"absolute": -1}, {"absolute": True}, {"typo": 1}, "0.5", 0.5]:
        with pytest.raises(ComparisonInputError):
            parse_tolerance(bad)


# ───────────────────────── column grain (#799) ──────────────────────


def _col_res(**kw: Any) -> Any:
    from backend.app.datasources.comparison import compare_columns

    kw.setdefault("keys", ["id"])
    source = kw.pop("source")
    target = kw.pop("target")
    return compare_columns(source, target, **kw)


def test_columns_grain_buckets_fdc_parity() -> None:
    # id=1: a matches, b mismatches. id=2: a null-in-target (additional_in_source
    # for column a), b matches. id=3 only in source (its non-null values are
    # additional_in_source). id=4 only in target.
    source = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"], "b": [1, 2, None]})
    target = pd.DataFrame({"id": [1, 2, 4], "a": ["x", None, "q"], "b": [9, 2, 4]})
    res = _col_res(source=source, target=target)
    assert res.per_column["a"] == {
        "matched": 1,
        "mismatched": 0,
        "additional_in_source": 2,  # id=2 (null opposite) + id=3 (row only in source)
        "additional_in_target": 1,  # id=4
    }
    assert res.per_column["b"] == {
        "matched": 1,
        "mismatched": 1,  # id=1: 1 vs 9
        "additional_in_source": 0,  # id=3's b is NULL → counts nowhere (dropna parity)
        "additional_in_target": 1,  # id=4
    }
    assert res.mismatched_values == 1
    assert res.additional_in_source_values == 2
    assert res.additional_in_target_values == 2
    assert not res.success
    # badness = non-matched / all counted slots = 5 / 7
    assert res.mismatch_percent == round(5 / 7 * 100.0, 4)


def test_columns_grain_success_and_samples_shape() -> None:
    source = pd.DataFrame({"id": [1], "a": ["x"]})
    target = pd.DataFrame({"id": [1], "a": ["y"]})
    res = _col_res(source=source, target=target)
    assert res.sample_mismatched == [{"id": "1", "a_src": "x", "a_tgt": "y"}]

    clean = _col_res(source=source, target=source.copy())
    assert clean.success and clean.mismatch_percent == 0.0


def test_columns_grain_requires_shared_columns() -> None:
    source = pd.DataFrame({"id": [1], "only_src": [1]})
    target = pd.DataFrame({"id": [1], "only_tgt": [2]})
    with pytest.raises(ComparisonInputError, match="shared non-key column"):
        _col_res(source=source, target=target)


def test_columns_grain_tolerance_applies() -> None:
    from backend.app.datasources.comparison import Tolerance

    source = pd.DataFrame({"id": [1], "v": [0.1]})
    # float32 round-trip of 0.1 — the #808-review scenario tolerance exists for.
    target = pd.DataFrame({"id": [1], "v": [0.10000000149011612]})
    exact = _col_res(source=source, target=target)
    assert exact.mismatched_values == 1
    tolerant = _col_res(source=source, target=target, tolerance=Tolerance(relative=1e-6))
    assert tolerant.success


# ───────────────────────── #812 review findings ─────────────────────


def test_uint64_beyond_int64_falls_back_to_float_canonical() -> None:
    import numpy as np

    big = np.array([2**63 + 5, 7], dtype="uint64")
    source = pd.DataFrame({"id": [1, 2], "v": big})
    target = pd.DataFrame({"id": [1, 2], "v": big.copy()})
    res = _res(source=source, target=target)
    assert res.success  # no TypeError, identical data reconciles


def test_reserved_position_names_refused() -> None:
    source = pd.DataFrame({"__dataq_pos": [1], "v": ["a"]})
    target = pd.DataFrame({"__dataq_pos": [1], "v": ["a"]})
    with pytest.raises(ComparisonInputError, match="reserved"):
        _res(source=source, target=target, keys=["__dataq_pos"])


def test_key_shadowed_by_sample_suffix_refused() -> None:
    # key 'v_src' + compared column 'v' → sample keys collide.
    source = pd.DataFrame({"v_src": [1, 2], "v": ["a", "b"]})
    target = pd.DataFrame({"v_src": [1, 2], "v": ["a", "X"]})
    with pytest.raises(ComparisonInputError, match="v_src"):
        _res(source=source, target=target, keys=["v_src"])
