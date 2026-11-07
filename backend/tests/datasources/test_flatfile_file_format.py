"""#2243: a flat-file target's explicit `file_format` reaches every run-path read, not only the
profiler — real parsing, with the store seams stubbed over one canned object."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from backend.app.datasources import flatfile, registry
from backend.app.datasources.base import CheckSpec, MonitorSpec, SampleSpec, TargetShapeError
from backend.app.services import run_admission
from backend.tests.support.fake_secret_store import FakeSecretStore

_CSV = b"id,amt\n1,10\n2,20\n3,30\n"
_DAT = "exports/orders.dat"


def _store(monkeypatch: pytest.MonkeyPatch, content: bytes = _CSV) -> None:
    monkeypatch.setattr(
        flatfile,
        "file_stat",
        lambda **k: flatfile.FileStat(
            last_modified=datetime(2026, 11, 1, tzinfo=UTC), size=len(content)
        ),
    )
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: content)
    monkeypatch.setattr(flatfile, "object_size", lambda **k: len(content))
    monkeypatch.setattr(
        flatfile, "read_range", lambda *, start, length, **k: content[start : start + length]
    )


def _runner(file_format: str | None, sampling: SampleSpec | None = None) -> Any:
    return flatfile.FlatFileCheckRunner(
        conn_type="s3", config={}, secret="s", sampling=sampling, file_format=file_format
    )


_NOT_NULL = [CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})]


def test_resolve_format_prefers_the_explicit_format() -> None:
    assert flatfile.resolve_format(_DAT, "csv") == "csv"
    assert flatfile.resolve_format("x.csv", "parquet") == "parquet"
    assert flatfile.resolve_format("x.parquet", None) == "parquet"
    assert flatfile.resolve_format(_DAT, None) is None
    assert flatfile.resolve_format("x.csv", "xml") is None


def test_checks_read_an_extensionless_key_in_its_target_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(monkeypatch)
    outcome = _runner("csv").run_checks(table=_DAT, schema=None, checks=_NOT_NULL)
    assert outcome.success is True and outcome.checks[0].success is True


def test_without_a_format_the_same_key_still_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(monkeypatch)
    with pytest.raises(ValueError, match="unsupported flat-file format"):
        flatfile.read_dataframe(conn_type="s3", config={}, path=_DAT, secret="s")


def test_the_explicit_format_overrides_a_misleading_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buf = io.BytesIO()
    pd.DataFrame({"id": [1, 2]}).to_parquet(buf)
    _store(monkeypatch, buf.getvalue())
    df = flatfile.read_dataframe(
        conn_type="s3", config={}, path="exports/orders.csv", secret="s", file_format="parquet"
    )
    assert list(df["id"]) == [1, 2]


def test_a_sampled_read_uses_the_target_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch)
    sampled = _runner("csv", SampleSpec(strategy="random", rows=2, seed=7))
    outcome = sampled.run_checks(table=_DAT, schema=None, checks=_NOT_NULL)
    assert outcome.checks[0].success is True


def test_the_volume_monitor_counts_an_extensionless_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch)
    out = _runner("csv").run_monitors(
        table=_DAT, schema=None, monitors=[MonitorSpec(kind="volume", config={"min_rows": 1})]
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None and out[0].observed_value["row_count"] == 3


def test_the_target_carries_its_format_and_refuses_an_unknown_one() -> None:
    resolved = registry.resolve_target_shape("s3", {"path": _DAT, "file_format": "csv"})
    assert resolved.file_format == "csv"
    batch = registry.resolve_target_shape(
        "s3", {"prefix": "exports/", "pattern": r"orders_\d+\.dat", "file_format": "json"}
    )
    assert batch.file_format == "json"
    with pytest.raises(TargetShapeError, match="file_format"):
        registry.resolve_target_shape("s3", {"path": _DAT, "file_format": "xml"})


def test_the_runner_factory_passes_the_format_to_the_flatfile_runner() -> None:
    store = FakeSecretStore()
    store.set("ref", "secret")
    runner = registry.build_check_runner(
        conn_type="s3",
        config={"bucket": "b"},
        secret_ref="ref",
        secret_store=store,
        file_format="parquet",
    )
    assert runner._file_format == "parquet"  # type: ignore[attr-defined]


def test_admission_prices_the_read_as_its_target_format() -> None:
    from backend.app.core.config import get_settings

    settings = get_settings()
    assert (
        run_admission._settings_expansion(_DAT, "parquet")
        == settings.run_admission_expansion_parquet
    )
    assert run_admission._settings_expansion(_DAT) == settings.run_admission_expansion_default


def test_schema_drift_introspects_with_the_suite_targets_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from backend.app.services import schema_drift

    seen: dict[str, Any] = {}

    def _introspect(connection: Any, **kw: Any) -> list[Any]:
        seen.update(kw)
        raise schema_drift.SchemaIntrospectionError("stop here")

    monkeypatch.setattr(schema_drift, "introspect_columns", _introspect)
    suite = SimpleNamespace(target={"path": _DAT, "file_format": "csv"})
    session = SimpleNamespace(get=lambda model, key: suite)
    executor = schema_drift.build_schema_drift_executor(
        session,  # type: ignore[arg-type]
        connection=SimpleNamespace(type="s3"),  # type: ignore[arg-type]
        target_table=_DAT,
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(),
        run=SimpleNamespace(suite_id="s1"),  # type: ignore[arg-type]
    )
    executor(SimpleNamespace(id="c1", config={}))  # type: ignore[arg-type]
    assert seen["file_format"] == "csv"


def test_a_frame_reading_monitor_uses_the_target_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, b"id,load_ts\n1,2026-06-29T00:00:00\n2,2026-06-28T00:00:00\n")
    out = _runner("csv").run_monitors(
        table=_DAT,
        schema=None,
        monitors=[MonitorSpec(kind="freshness", config={"column": "load_ts"})],
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-06-29")
