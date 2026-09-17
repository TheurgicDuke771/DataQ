"""Unit tests for run_target.resolve_target / validate_target / materialize_path."""

from typing import Any

import pytest

from backend.app.datasources.base import ResolvedTarget  # moved in #727
from backend.app.datasources.flatfile import BatchPreviewResult
from backend.app.services import run_target
from backend.app.services.run_target import (
    BatchPreviewFailedError,
    BatchPreviewNoDataError,
    SuiteTargetInvalidError,
    resolve_target,
    validate_target,
)
from backend.tests.support.fake_secret_store import FakeSecretStore


def test_snowflake_resolves_table_and_optional_schema() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS", "schema": "SALES"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", "SALES", None)


def test_snowflake_schema_optional() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", None, None)


def test_unity_catalog_requires_catalog_and_table() -> None:
    r = resolve_target("unity_catalog", {"catalog": "main", "schema": "sales", "table": "orders"})
    assert (r.table, r.schema, r.catalog) == ("orders", "sales", "main")


def test_unity_catalog_missing_catalog_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("unity_catalog", {"table": "orders"})


def test_iceberg_folds_namespace_into_identifier() -> None:
    r = resolve_target("iceberg", {"namespace": "sales", "table": "orders"})
    # namespace.table rides `table`; Iceberg has no SQL schema/catalog.
    assert (r.table, r.schema, r.catalog) == ("sales.orders", None, None)


def test_iceberg_namespace_optional() -> None:
    r = resolve_target("iceberg", {"table": "orders"})
    assert (r.table, r.schema, r.catalog) == ("orders", None, None)


def test_iceberg_missing_table_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("iceberg", {"namespace": "sales"})


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_path_rides_table_slot(conn_type: str) -> None:
    # The CheckRunner interface is table-shaped; the file path is the `table`.
    r = resolve_target(conn_type, {"path": "data/orders.csv"})
    assert (r.table, r.schema, r.catalog) == ("data/orders.csv", None, None)


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_missing_path_raises(conn_type: str) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "orders"})  # SQL field, wrong datasource


def test_snowflake_missing_table_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"schema": "SALES"})


def test_blank_table_is_rejected() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"table": "   "})


@pytest.mark.parametrize("target", [None, {}])
def test_targetless_suite_raises(target: dict[str, str] | None) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", target)


@pytest.mark.parametrize("conn_type", ["adf", "airflow"])
def test_orchestration_types_have_no_run_path(conn_type: str) -> None:
    # ADF / Airflow are orchestration providers, never suite datasources.
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "x"})


def test_validate_target_is_resolve_without_return() -> None:
    validate_target("snowflake", {"table": "ORDERS"})  # no raise
    with pytest.raises(SuiteTargetInvalidError):
        validate_target("snowflake", {"schema": "SALES"})


# ───────────────────────── flat-file batch spec (A4) ───────────────


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_batch_latest_default(conn_type: str) -> None:
    r = resolve_target(
        conn_type, {"prefix": "orders/", "pattern": r"orders_(\d{4}-\d{2}-\d{2})\.csv"}
    )
    assert r.table == "" and r.batch is not None
    assert (r.batch.prefix, r.batch.strategy, r.batch.batch) == ("orders/", "latest", None)
    assert r.batch.pattern == r"orders_(\d{4}-\d{2}-\d{2})\.csv"


def test_flatfile_batch_prefix_optional_defaults_empty() -> None:
    r = resolve_target("s3", {"pattern": r"(\d+)\.csv"})
    assert r.batch is not None and r.batch.prefix == ""


def test_flatfile_batch_specific_requires_batch_key() -> None:
    r = resolve_target(
        "s3", {"pattern": r"(\d+)\.csv", "strategy": "specific", "batch": "2026-06-01"}
    )
    assert r.batch is not None and r.batch.strategy == "specific" and r.batch.batch == "2026-06-01"


def test_flatfile_batch_specific_without_batch_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "specific"})


def test_flatfile_batch_latest_ignores_batch_key() -> None:
    # 'batch' only applies to 'specific'; under 'latest' it is dropped.
    r = resolve_target("s3", {"pattern": r"(\d+)\.csv", "batch": "ignored"})
    assert r.batch is not None and r.batch.batch is None


def test_flatfile_batch_unknown_strategy_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "newest"})


def test_flatfile_batch_blank_pattern_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": "   "})


def test_flatfile_batch_non_string_prefix_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "prefix": 123})


def test_validate_target_accepts_batch_spec() -> None:
    validate_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "latest"})  # no raise


def test_flatfile_ambiguous_path_and_pattern_raises() -> None:
    # A literal path and a batch pattern are mutually exclusive — both set is a
    # configuration error, not a silent batch win.
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"path": "data/o.csv", "pattern": r"(\d+)\.csv"})


def test_flatfile_batch_invalid_regex_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"orders_([0-9.csv"})  # unbalanced group


def test_flatfile_batch_specific_without_capture_group_raises() -> None:
    # 'specific' matches on the first capture group; a group-less pattern could
    # never match a key → it would skip forever, masking the misconfig.
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"orders\.csv", "strategy": "specific", "batch": "x"})


# ── ReDoS-shape rejection (#1243) ────────────────────────────────────────────
# Enforced in `registry._batch_spec`, the single place BOTH `validate_target`
# (save) and `preview_batch` (preview, via `resolve_target`) compile the
# pattern — so a save-time check that agrees with preview requires nothing
# extra here beyond going through `resolve_target` itself.


def test_flatfile_batch_pattern_over_length_cap_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError, match="at most"):
        resolve_target("s3", {"pattern": "a" * 201})


def test_flatfile_batch_pattern_at_length_cap_is_accepted() -> None:
    resolve_target("s3", {"pattern": "a" * 200})  # no raise


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+$",
        r"([a-z]*)+",
        r"(a*)*",
        r"orders_(\d+)+\.csv",
    ],
)
def test_flatfile_batch_nested_quantifier_pattern_is_rejected(pattern: str) -> None:
    with pytest.raises(SuiteTargetInvalidError, match="nested"):
        resolve_target("s3", {"pattern": pattern})


@pytest.mark.parametrize(
    "pattern",
    [
        r"orders_(\d+)\.csv",
        r"orders_(\d{4}-\d{2}-\d{2})\.csv",
        r"(ab)+",
        r"a+",
    ],
)
def test_flatfile_batch_ordinary_quantified_patterns_are_not_flagged(pattern: str) -> None:
    resolve_target("s3", {"pattern": pattern})  # no raise


def test_a_known_catastrophic_backtracking_pattern_never_reaches_a_match_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`(a+)+$` against a long run of 'a's is the textbook ReDoS trigger — Python's
    `re` has no match timeout, so the only defense available is refusing the
    SHAPE before a match is ever attempted (#1243). This is the end-to-end
    regression test for the actual attack surface the issue describes: the
    preview endpoint, given the pattern AND a store that would hand it a long,
    hostile key. It asserts the listing seam is never even called (not just
    "no exception") and that the whole call is wall-clock bounded — a real
    match of this pattern against a hostile key would blow straight through
    any reasonable bound (confirmed once, out-of-process: the same pattern
    against 30 'a's plus a non-matching suffix does not return within 3 seconds
    when actually matched).
    """
    import time

    def _must_not_be_called(**_: Any) -> Any:  # pragma: no cover - must never run
        raise AssertionError(
            "flatfile.resolve_batch_file_preview must not be reached for a rejected pattern"
        )

    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview", _must_not_be_called
    )
    long_hostile_key = "a" * 5000 + "!"  # never terminates a `(a+)+$` match attempt

    start = time.monotonic()
    with pytest.raises(SuiteTargetInvalidError):
        run_target.preview_batch(
            "s3",
            {},
            prefix=long_hostile_key,
            pattern=r"(a+)+$",
            strategy="latest",
            batch=None,
            secret_ref="kv-ref",
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"pattern-shape rejection took {elapsed}s — no longer bounded"


# ───────────────────────── materialize_path (A4 live step) ─────────


def test_materialize_path_passthrough_for_non_batch() -> None:
    # SQL / literal flat-file targets have no batch → table returned unchanged,
    # and the store is never consulted (no listing needed).
    resolved = ResolvedTarget(table="ORDERS", schema="SALES", catalog=None)
    out = run_target.materialize_path(
        "snowflake",
        {},
        resolved,
        secret_ref=None,
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out == "ORDERS"


def test_materialize_path_resolves_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = resolve_target("s3", {"prefix": "orders/", "pattern": r"orders_(\d+)\.csv"})
    captured: dict[str, Any] = {}

    def _fake_resolve(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "orders/orders_20260601.csv"

    monkeypatch.setattr("backend.app.datasources.flatfile.resolve_batch_file", _fake_resolve)
    out = run_target.materialize_path(
        "s3",
        {"bucket": "b"},
        resolved,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out == "orders/orders_20260601.csv"
    # the resolved BatchSpec + resolved secret are threaded to the lister
    assert captured["prefix"] == "orders/" and captured["strategy"] == "latest"
    assert captured["secret"] == "secret-value" and captured["conn_type"] == "s3"


def test_materialize_path_batch_without_secret_raises() -> None:
    resolved = resolve_target("s3", {"pattern": r"(\d+)\.csv"})
    with pytest.raises(SuiteTargetInvalidError):
        run_target.materialize_path(
            "s3",
            {},
            resolved,
            secret_ref=None,
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )


# ── #727: the registry is the single place a datasource is declared ──────────


def test_every_datasource_adapter_has_a_target_resolver() -> None:
    """The "adding a datasource is one registry entry" contract, enforced."""
    from backend.app.datasources.registry import _ADAPTERS, _TARGET_RESOLVERS
    from backend.app.orchestration.registry import _PROVIDERS

    datasources = {t for t in _ADAPTERS if t not in _PROVIDERS}

    assert datasources, "no datasource types registered — the fixture is wrong, not the code"
    assert datasources == set(_TARGET_RESOLVERS), (
        "a datasource adapter without a target resolver saves suites that cannot run: "
        f"missing {sorted(datasources - set(_TARGET_RESOLVERS))}"
    )


def test_an_orchestration_provider_still_has_no_run_path() -> None:
    """The rejection that used to be the chain's fallthrough must survive the move."""
    from backend.app.orchestration.registry import _PROVIDERS

    for provider in _PROVIDERS:
        with pytest.raises(SuiteTargetInvalidError) as exc:
            resolve_target(provider, {"table": "x"})
        assert "no run path" in str(exc.value)


# ───────────────────────── preview_batch (#1193, budget #1243) ──────


def test_preview_batch_resolves_via_the_live_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    # Threads through resolve_target (shape validation) + the budget-bounded
    # preview listing (#1243) — the run path's own `materialize_path` is untouched.
    captured: dict[str, Any] = {}

    def _fake_resolve(**kwargs: Any) -> BatchPreviewResult:
        captured.update(kwargs)
        return BatchPreviewResult(path="orders/orders_20260601.csv", scanned=3, truncated=False)

    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview", _fake_resolve
    )
    out = run_target.preview_batch(
        "s3",
        {"bucket": "b"},
        prefix="orders/",
        pattern=r"orders_(\d+)\.csv",
        strategy="latest",
        batch=None,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out.path == "orders/orders_20260601.csv"
    assert out.scanned == 3 and out.truncated is False
    assert captured["prefix"] == "orders/" and captured["strategy"] == "latest"
    assert captured["secret"] == "secret-value" and captured["conn_type"] == "s3"
    # The preview budget comes from Settings, not a hardcoded/run-path value.
    from backend.app.core.config import get_settings

    settings = get_settings()
    assert captured["max_objects"] == settings.batch_preview_max_objects
    assert captured["max_seconds"] == settings.batch_preview_max_seconds


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_preview_batch_accepts_flatfile_types(
    conn_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview",
        lambda **_: BatchPreviewResult(path="x/orders_1.csv", scanned=1, truncated=False),
    )
    out = run_target.preview_batch(
        conn_type,
        {},
        prefix="",
        pattern=r"orders_(\d+)\.csv",
        strategy="latest",
        batch=None,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out.path == "x/orders_1.csv"


@pytest.mark.parametrize("conn_type", ["snowflake", "unity_catalog", "iceberg", "adf", "airflow"])
def test_preview_batch_rejects_non_flatfile_connections(
    conn_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No second hardcoded flat-file type set here: a batch spec carries no `table`/`path`, so every
    # SQL datasource's shape rejects it and an orchestration provider has no run path at all.
    def _boom(**_: Any) -> BatchPreviewResult:  # pragma: no cover - must never be reached
        raise AssertionError(
            "resolve_batch_file_preview must not be called for a non-flat-file type"
        )

    monkeypatch.setattr("backend.app.datasources.flatfile.resolve_batch_file_preview", _boom)
    with pytest.raises(SuiteTargetInvalidError) as exc:
        run_target.preview_batch(
            conn_type,
            {},
            prefix="",
            pattern=r"(\d+)\.csv",
            strategy="latest",
            batch=None,
            secret_ref="kv-ref",
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )
    assert exc.value.code == "suite_target_invalid"


def test_preview_batch_invalid_regex_raises_before_any_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_: Any) -> BatchPreviewResult:  # pragma: no cover - must never be reached
        raise AssertionError("resolve_batch_file_preview must not be called for a bad pattern")

    monkeypatch.setattr("backend.app.datasources.flatfile.resolve_batch_file_preview", _boom)
    with pytest.raises(SuiteTargetInvalidError):
        run_target.preview_batch(
            "s3",
            {},
            prefix="",
            pattern=r"orders_([0-9.csv",  # unbalanced group
            strategy="latest",
            batch=None,
            secret_ref="kv-ref",
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )


def test_preview_batch_specific_without_capture_group_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        run_target.preview_batch(
            "s3",
            {},
            prefix="",
            pattern=r"orders\.csv",
            strategy="specific",
            batch="x",
            secret_ref="kv-ref",
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )


def _preview_s3(monkeypatch: pytest.MonkeyPatch, *, exc: Exception) -> Any:
    """Drive `preview_batch` on an s3 connection whose bounded listing raises `exc`."""

    def _raise(**_: Any) -> BatchPreviewResult:
        raise exc

    monkeypatch.setattr("backend.app.datasources.flatfile.resolve_batch_file_preview", _raise)
    return run_target.preview_batch(
        "s3",
        {},
        prefix="orders/",
        pattern=r"orders_(\d+)\.csv",
        strategy="latest",
        batch=None,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )


def test_preview_batch_reports_no_match_within_a_completed_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listing finished (within budget) and nothing matched — a definitive
    "no data yet" (#122), same 422 as before the preview budget existed.
    """
    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview",
        lambda **_: BatchPreviewResult(path=None, scanned=4, truncated=False),
    )
    with pytest.raises(BatchPreviewNoDataError):
        run_target.preview_batch(
            "s3",
            {},
            prefix="orders/",
            pattern=r"orders_(\d+)\.csv",
            strategy="latest",
            batch=None,
            secret_ref="kv-ref",
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )


def test_preview_batch_reports_a_truncated_scan_with_no_match_as_an_honest_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget ran out before the listing finished — this must NOT raise
    `BatchPreviewNoDataError` (that would falsely claim "no file exists anywhere
    under this prefix"); it is an honest "didn't get far enough to say" (#1243).
    """
    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview",
        lambda **_: BatchPreviewResult(path=None, scanned=2000, truncated=True),
    )
    out = run_target.preview_batch(
        "s3",
        {},
        prefix="orders/",
        pattern=r"orders_(\d+)\.csv",
        strategy="latest",
        batch=None,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out.path is None
    assert out.scanned == 2000
    assert out.truncated is True


def test_preview_batch_reports_a_truncated_scan_with_a_provisional_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Budget hit AFTER a match was already seen: the best-so-far path is still a
    useful hint, but `truncated=True` says it isn't a guaranteed answer (a later,
    unscanned object could sort ahead of it under 'latest').
    """
    monkeypatch.setattr(
        "backend.app.datasources.flatfile.resolve_batch_file_preview",
        lambda **_: BatchPreviewResult(
            path="orders/orders_2026-06-01.csv", scanned=2000, truncated=True
        ),
    )
    out = run_target.preview_batch(
        "s3",
        {},
        prefix="orders/",
        pattern=r"orders_(\d+)\.csv",
        strategy="latest",
        batch=None,
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
    )
    assert out.path == "orders/orders_2026-06-01.csv"
    assert out.truncated is True


def test_preview_batch_never_echoes_a_bare_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare ValueError is NOT a flat-file batch signal — it is an arbitrary adapter/driver error,
    # and boto3/azure text routinely carries the endpoint, the account, or a token fragment.
    leaky = "Invalid credentials for https://acct.blob.core.windows.net/?sig=SECRETTOKEN"
    with pytest.raises(BatchPreviewFailedError) as exc:
        _preview_s3(monkeypatch, exc=ValueError(leaky))
    assert exc.value.status_code == 502
    assert "SECRETTOKEN" not in str(exc.value)
    assert "SECRETTOKEN" not in str(exc.value.detail)
    assert "blob.core.windows.net" not in str(exc.value.detail)


def test_preview_batch_maps_an_arbitrary_failure_to_a_classified_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(BatchPreviewFailedError) as exc:
        _preview_s3(monkeypatch, exc=RuntimeError("connection timed out to 10.1.2.3:443"))
    assert exc.value.status_code == 502
    assert "10.1.2.3" not in str(exc.value.detail)


def test_preview_batch_without_secret_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        run_target.preview_batch(
            "s3",
            {},
            prefix="",
            pattern=r"(\d+)\.csv",
            strategy="latest",
            batch=None,
            secret_ref=None,
            secret_store=FakeSecretStore(default="secret-value", raise_on_write=True),
        )
