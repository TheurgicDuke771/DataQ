"""Warehouse + Iceberg-curve tier bodies (#1992).

The live-warehouse bodies cannot be exercised without a warehouse; what IS
exercised here is everything that decides whether they run, what they say when
they do not, and that nothing they emit can carry a credential. The Iceberg
curve needs no credential at all, so its body runs for real against a local
pyiceberg catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.scripts import perf_baseline
from backend.scripts.perf import cases_warehouse, catalog, datagen, harness

_SECRET_VARS = ("PERF_SF_SECRET", "PERF_UC_SECRET", "PERF_ICEBERG_SECRET")

#: The environment each live tier needs, as its REGISTRATION reads it.
_TIER_ENV: dict[str, tuple[str, ...]] = {
    "warehouse.snowflake.1m.5checks": (*cases_warehouse._SF_ENV, "PERF_SF_TABLE_1M"),
    "warehouse.snowflake.50m.5checks": (*cases_warehouse._SF_ENV, "PERF_SF_TABLE_50M"),
    "warehouse.profiler.snowflake.wide": (*cases_warehouse._SF_ENV, "PERF_SF_WIDE_TABLE"),
    "warehouse.unity_catalog.1m.5checks": cases_warehouse._UC_ENV,
    "warehouse.unity_catalog.1m.5checks.frame": cases_warehouse._UC_ENV,
    "warehouse.iceberg.1m.5checks": cases_warehouse._ICEBERG_ENV,
}


def _env_complete(case_id: str) -> bool:
    return cases_warehouse._gate(*_TIER_ENV[case_id]) is None


class TestGating:
    def test_a_tier_with_no_credentials_names_every_variable_it_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PERF_SF_ACCOUNT", raising=False)
        monkeypatch.setenv("PERF_SF_USER", "u")
        reason = cases_warehouse._gate("PERF_SF_ACCOUNT", "PERF_SF_USER", "PERF_SF_ROLE")
        assert reason is not None
        assert "PERF_SF_ACCOUNT" in reason and "PERF_SF_ROLE" in reason
        assert "PERF_SF_USER" not in reason

    def test_a_blank_variable_is_missing_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An env var set to "" is the shape a half-written export leaves behind;
        # treating it as present would fail deep inside a driver instead of here.
        monkeypatch.setenv("PERF_SF_ACCOUNT", "   ")
        assert cases_warehouse._gate("PERF_SF_ACCOUNT") is not None

    def test_a_fully_configured_tier_is_not_gated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERF_SF_ACCOUNT", "acct")
        assert cases_warehouse._gate("PERF_SF_ACCOUNT") is None

    def test_a_live_tier_is_skipped_exactly_when_its_environment_is_incomplete(self) -> None:
        """Asserted as an EQUIVALENCE, not as "everything is skipped": this file
        must also pass on the harness box, where the variables are exported and
        the tiers correctly register as runnable.
        """
        live = catalog.select(families=["warehouse_run"]) + catalog.select(
            ids=["warehouse.profiler.snowflake.wide"]
        )
        assert len(live) == 6
        for case in live:
            configured = _env_complete(case.id)
            assert (case.skip_reason is None) is configured, case.id
            if case.skip_reason:
                assert "PERF_" in case.skip_reason


class TestCurveRegistration:
    def test_the_curve_covers_the_rungs_the_ceiling_sits_between(self) -> None:
        assert cases_warehouse.ICEBERG_CURVE_ROWS == (
            1_000_000,
            2_000_000,
            3_000_000,
            4_000_000,
            5_000_000,
        )
        ids = {c.id for c in catalog.select(tags=["iceberg_curve"])}
        assert ids == {f"iceberg_curve.{n}m.5checks" for n in (1, 2, 3, 4, 5)}

    def test_the_curve_is_never_in_the_ci_subset(self) -> None:
        # Each rung wants gigabytes and the whole point is that one of them dies.
        assert not {c.id for c in catalog.select(tags=["ci"])} & {
            c.id for c in catalog.select(tags=["iceberg_curve"])
        }

    def test_every_measured_tier_lifts_the_cap_it_would_otherwise_hit(self) -> None:
        for case in catalog.select(tags=["iceberg_curve"]) + catalog.select(
            families=["warehouse_run"]
        ):
            assert case.env["RUN_MAX_SCAN_ROWS"] == "0"
            assert case.env["RUN_MAX_SCAN_ROWS_ICEBERG"] == "0"

    def test_the_uc_pair_differs_only_in_the_pushdown_flag(self) -> None:
        by_id = {c.id: c for c in catalog.select(families=["warehouse_run"])}
        pushdown = by_id["warehouse.unity_catalog.1m.5checks"]
        frame = by_id["warehouse.unity_catalog.1m.5checks.frame"]
        assert pushdown.env["UC_SQL_PUSHDOWN"] == "true"
        assert frame.env["UC_SQL_PUSHDOWN"] == "false"

    def test_a_curve_rung_with_no_fixture_says_how_to_build_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        reason = cases_warehouse._iceberg_curve_gate(5_000_000)
        assert reason is not None
        assert "gen-iceberg --rows 5000000" in reason


class TestNoSecretReachesTheOutput:
    def test_no_configured_secret_value_appears_in_an_emitted_row(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The rows are written to a file and pasted into a doc, so a credential
        reaching one is a leak that outlives the run."""
        sentinel = "s3cret-sentinel-value"
        for variable in _SECRET_VARS:
            monkeypatch.setenv(variable, sentinel)
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        rows = [
            perf_baseline._skip_row(case, "sha", "2026-01-01T00:00:00+00:00")
            for case in catalog.select(families=["warehouse_run"])
        ]
        # The sentinel is on the LAST stderr line on purpose: a driver's dying
        # words are a connection URL, and "the last line" is the tempting thing
        # to copy into the row.
        rows.append(
            perf_baseline._killed_row(
                catalog.select(families=["warehouse_run"])[0],
                harness.CaseFailedError("c", -9, f"Killed\nconnecting with {sentinel}"),
                "sha",
                "2026-01-01T00:00:00+00:00",
            )
        )
        assert sentinel not in json.dumps(rows)

    def test_the_gate_reason_names_the_variable_never_its_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PERF_SF_ACCOUNT", "acct-1")
        monkeypatch.delenv("PERF_SF_USER", raising=False)
        reason = cases_warehouse._gate("PERF_SF_ACCOUNT", "PERF_SF_USER")
        assert reason is not None and "acct-1" not in reason


class TestKilledCases:
    def test_a_killed_child_is_a_row_and_the_run_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rung that dies IS the measurement — ending the run there would throw
        away every rung above it, which is the rest of the curve."""
        cases = catalog.select(ids=["scheduler.10", "batch_resolve.1k"])
        assert len(cases) == 2
        calls: list[str] = []

        def fake_run(case: Any) -> dict[str, Any]:
            calls.append(case.id)
            if case.id == "batch_resolve.1k":
                raise harness.CaseFailedError(case.id, -9, "\nKilled")
            return {
                "case": case.id,
                "metrics": [{"name": "m", "value": 1.0, "unit": "x", "gate": "observe"}],
            }

        monkeypatch.setattr(perf_baseline, "run_in_subprocess", fake_run)
        report = perf_baseline.run_cases(cases, repeat=1)
        assert calls == ["batch_resolve.1k", "scheduler.10"]  # it kept going
        killed = [r for r in report["rows"] if r["status"] == "killed"]
        assert len(killed) == 1
        assert killed[0]["case"] == "batch_resolve.1k"
        assert killed[0]["signal"] == 9
        assert killed[0]["oom_killed"] is True
        assert killed[0]["value"] is None

    @pytest.mark.parametrize(
        ("returncode", "signal", "oom"),
        [(-9, 9, True), (137, 9, True), (-15, 15, False), (1, None, False)],
    )
    def test_both_kill_conventions_decode_to_the_same_signal(
        self, returncode: int, signal: int | None, oom: bool
    ) -> None:
        # A bare fork reports -SIGKILL; a container runtime reports 128+SIGKILL.
        # Reading only one would report a docker-rig OOM as an ordinary failure.
        exc = harness.CaseFailedError("c", returncode, "")
        assert exc.signal == signal
        assert exc.oom_killed is oom

    def test_a_killed_row_is_not_gated_but_does_not_read_as_measured(self) -> None:
        case = catalog.select(ids=["scheduler.10"])[0]
        row = perf_baseline._killed_row(
            case, harness.CaseFailedError(case.id, -9, "boom"), "sha", "t"
        )
        assert row["gate"] == "observe" and row["status"] == "killed"

    def test_an_ordinary_failure_is_raised_not_recorded_as_a_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A traceback is a bad table name, an expired credential or a moved seam.
        Recording it as a ceiling would turn a misconfiguration into a finding,
        with the run still reporting success."""

        def fake_run(case: Any) -> dict[str, Any]:
            raise harness.CaseFailedError(case.id, 1, "Traceback: no such table")

        monkeypatch.setattr(perf_baseline, "run_in_subprocess", fake_run)
        with pytest.raises(harness.CaseFailedError):
            perf_baseline.run_cases(catalog.select(ids=["scheduler.10"]), repeat=1)

    def test_a_timeout_is_a_ceiling_arriving_as_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def fake_subprocess_run(*_a: Any, **_kw: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd=["x"], timeout=1.0)

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        with pytest.raises(harness.CaseFailedError) as raised:
            harness.spawn("scheduler.10", env={}, timeout=1.0)
        assert raised.value.timed_out is True
        assert raised.value.is_ceiling is True
        assert raised.value.signal is None  # nothing killed it — it never stopped
        assert "timed out after 1s" in raised.value.describe()

    def test_the_reason_classifies_rather_than_quoting_the_child(self) -> None:
        assert "killed by signal 9" in harness.CaseFailedError("c", -9, "").describe()
        assert "exited 3" in harness.CaseFailedError("c", 3, "").describe()

    def test_check_fails_on_a_killed_case_instead_of_printing_budget_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        """A killed row carries the `observe` gate, so the budget skips it — and
        would otherwise pass a case that produced no number at all."""

        def fake_run(case: Any) -> dict[str, Any]:
            raise harness.CaseFailedError(case.id, -9, "")

        monkeypatch.setattr(perf_baseline, "run_in_subprocess", fake_run)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"rows": []}))
        rc = perf_baseline.main(["check", "--case", "scheduler.10", "--baseline", str(baseline)])
        assert rc == 1
        assert "produced no measurement" in capsys.readouterr().out


class TestIcebergCurveBody:
    """The curve body runs for real — a local `pyiceberg` catalog, DataQ's own
    runner, no mocks and no credential.
    """

    def test_a_small_rung_measures_the_real_runner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("RUN_MAX_SCAN_ROWS_ICEBERG", "0")
        from backend.app.core.config import get_settings

        get_settings.cache_clear()
        assert datagen.build_iceberg_dataset(1_000, echo=lambda _m: None)
        metrics = {m.name: m for m in cases_warehouse._run_iceberg_curve(1_000)}
        assert metrics["checks_evaluated"].value == 5.0
        assert metrics["planned_rows"].value == 1_000.0
        assert metrics["frame_rows"].value == 1_000.0  # the whole snapshot, materialised
        assert metrics["run_wall_s"].value > 0

    def test_the_generator_is_idempotent_and_the_config_addresses_the_fixture(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path / "unbuilt"))
        assert datagen.iceberg_table_exists(500) is False
        # Asking must not CREATE the warehouse — registration asks on every run.
        assert not (tmp_path / "unbuilt").exists()
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        identifier = datagen.build_iceberg_dataset(500, echo=lambda _m: None)
        assert datagen.build_iceberg_dataset(500, echo=lambda _m: None) == identifier
        assert datagen.iceberg_table_exists(500) is True
        config = datagen.iceberg_connection_config()
        assert config["catalog_type"] == "sql"
        assert str(tmp_path) in config["warehouse"]

    def test_a_chunked_build_keeps_line_id_unique(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`line_id` is what `expect_column_values_to_be_unique` asserts on. A key
        that restarts per append makes the tier evaluate the FAILING branch —
        different work from the flat-file tiers it is calibrated against, which
        distorts the peak-RSS number the curve exists to produce."""
        from backend.app.datasources.iceberg import IcebergConfig, read_iceberg_dataframe

        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(datagen, "ICEBERG_APPEND_CHUNK", 400)
        datagen.build_iceberg_dataset(1_000, echo=lambda _m: None)
        frame = read_iceberg_dataframe(
            IcebergConfig.model_validate(datagen.iceberg_connection_config()),
            None,
            datagen.iceberg_identifier(1_000),
        )
        assert len(frame) == 1_000
        assert frame["line_id"].nunique() == 1_000  # three chunks, one key space

    def test_an_interrupted_build_is_not_reused_as_the_fixture(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The table exists after the FIRST append, so without a completion marker
        a build interrupted at 400 of 1,000 would be measured, and labelled, as
        the full rung."""
        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(datagen, "ICEBERG_APPEND_CHUNK", 400)
        calls = {"n": 0}
        original = datagen._frame

        def explode_after_one_chunk(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt
            return original(*args, **kwargs)

        monkeypatch.setattr(datagen, "_frame", explode_after_one_chunk)
        with pytest.raises(KeyboardInterrupt):
            datagen.build_iceberg_dataset(1_000, echo=lambda _m: None)
        assert datagen.iceberg_table_exists(1_000) is False

        monkeypatch.setattr(datagen, "_frame", original)
        datagen.build_iceberg_dataset(1_000, echo=lambda _m: None)
        assert datagen.iceberg_table_exists(1_000) is True
        assert cases_warehouse._run_iceberg_curve(1_000)  # and it is the FULL rung
        metrics = {m.name: m.value for m in cases_warehouse._run_iceberg_curve(1_000)}
        assert metrics["planned_rows"] == 1_000.0

    def test_the_fixture_carries_the_flat_file_column_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The tiers are only comparable with the flat-file ones if the rows are
        # the same rows.
        from backend.app.datasources.iceberg import IcebergConfig, load_iceberg_table

        monkeypatch.setenv("PERF_DATA_DIR", str(tmp_path))
        datagen.build_iceberg_dataset(100, echo=lambda _m: None)
        table = load_iceberg_table(
            IcebergConfig.model_validate(datagen.iceberg_connection_config()),
            None,
            datagen.iceberg_identifier(100),
        )
        assert tuple(field.name for field in table.schema().fields) == datagen.COLUMNS


class TestSeamGuard:
    def test_a_moved_reader_seam_is_fatal_rather_than_zero_rows(self) -> None:
        """Silently skipping a seam that moved would report "the worker held
        nothing" about a read nobody measured."""
        counters = cases_warehouse._Counters()
        with pytest.raises(cases_warehouse.SeamMissingError, match="_gone"):
            with cases_warehouse._measured_frames(object(), ("_gone",), counters):
                pass  # pragma: no cover — the context manager raises on entry
