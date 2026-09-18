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

    def test_every_warehouse_tier_is_skipped_with_a_reason_on_a_bare_machine(self) -> None:
        # Registration reads the environment at import time, and this suite runs
        # without warehouse credentials — so every live tier must be SKIPPED with
        # a stated reason rather than absent or attempted.
        live = catalog.select(families=["warehouse_run"]) + catalog.select(
            ids=["warehouse.profiler.snowflake.wide"]
        )
        assert len(live) == 6
        for case in live:
            assert case.skip_reason, case.id
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
        rows.append(
            perf_baseline._killed_row(
                catalog.select(families=["warehouse_run"])[0],
                harness.CaseFailedError("c", -9, f"connecting with {sentinel}\nKilled"),
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
