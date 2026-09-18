"""Tests for the scale-baseline harness, the regression budget and the case registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.scripts import perf_baseline
from backend.scripts.perf import budget, cases_db, datagen, harness


def _row(
    case: str, metric: str, value: float, gate: str = "strict", **extra: Any
) -> dict[str, Any]:
    return {"case": case, "metric": metric, "value": value, "gate": gate, **extra}


class TestMetric:
    def test_rejects_an_unknown_gate(self) -> None:
        with pytest.raises(ValueError, match="unknown gate"):
            harness.Metric("x", 1.0, "s", "sometimes")

    @pytest.mark.parametrize("gate", harness.GATES)
    def test_accepts_every_declared_gate(self, gate: str) -> None:
        assert harness.Metric("x", 1.0, "s", gate).gate == gate


class TestSummarise:
    def test_median_and_coefficient_of_variation(self) -> None:
        stats = harness.summarise([10.0, 12.0, 11.0])
        assert stats["value"] == 11.0
        assert stats["min"] == 10.0
        assert stats["max"] == 12.0
        assert stats["n"] == 3
        assert 0.0 < stats["cov"] < 0.2

    def test_single_sample_has_no_variance(self) -> None:
        assert harness.summarise([4.0])["cov"] == 0.0

    def test_all_zero_samples_do_not_divide_by_zero(self) -> None:
        assert harness.summarise([0.0, 0.0])["cov"] == 0.0


class TestBudget:
    def test_an_unchanged_strict_counter_passes(self) -> None:
        base = [_row("c", "store_calls", 3.0)]
        violations, notes = budget.compare(base, [_row("c", "store_calls", 3.0)])
        assert violations == []
        assert notes == []

    def test_any_increase_in_a_strict_counter_fails(self) -> None:
        base = [_row("c", "store_calls", 3.0)]
        violations, _ = budget.compare(base, [_row("c", "store_calls", 4.0)])
        assert [v.metric for v in violations] == ["store_calls"]
        assert "store_calls 3 -> 4" in violations[0].describe()

    def test_a_decrease_never_fails(self) -> None:
        base = [_row("c", "store_calls", 9.0)]
        violations, _ = budget.compare(base, [_row("c", "store_calls", 1.0)])
        assert violations == []

    def test_peak_rss_is_allowed_a_band(self) -> None:
        base = [_row("c", "peak_rss_mib", 100.0, gate="band")]
        within, _ = budget.compare(base, [_row("c", "peak_rss_mib", 119.0, gate="band")])
        past, _ = budget.compare(base, [_row("c", "peak_rss_mib", 121.0, gate="band")])
        assert within == []
        assert [v.metric for v in past] == ["peak_rss_mib"]

    def test_wall_clock_is_recorded_but_never_gated(self) -> None:
        base = [_row("c", "wall_s", 1.0, gate="observe")]
        violations, notes = budget.compare(base, [_row("c", "wall_s", 100.0, gate="observe")])
        assert violations == []
        assert notes == []

    def test_a_new_metric_is_a_note_not_a_failure(self) -> None:
        violations, notes = budget.compare([], [_row("c", "store_calls", 1.0)])
        assert violations == []
        assert notes == ["c:store_calls is new — no baseline to compare against"]

    def test_a_gated_metric_missing_from_the_run_is_reported(self) -> None:
        violations, notes = budget.compare([_row("c", "store_calls", 1.0)], [])
        assert violations == []
        assert notes == ["c:store_calls is in the baseline but was not measured in this run"]

    def test_not_measured_rows_are_skipped_on_both_sides(self) -> None:
        skipped = _row("w", "not_measured", 0.0, status="not_measured")
        violations, notes = budget.compare([skipped], [skipped])
        assert (violations, notes) == ([], [])

    def test_the_tolerance_is_overridable(self) -> None:
        base = [_row("c", "peak_rss_mib", 100.0, gate="band")]
        fresh = [_row("c", "peak_rss_mib", 130.0, gate="band")]
        assert budget.compare(base, fresh, tolerances={"band": 0.5})[0] == []

    def test_narrowing_the_gates_drops_the_bands_ci_cannot_compare(self) -> None:
        base = [_row("c", "peak_rss_mib", 100.0, gate="band"), _row("c", "store_calls", 3.0)]
        fresh = [_row("c", "peak_rss_mib", 400.0, gate="band"), _row("c", "store_calls", 3.0)]
        assert budget.compare(base, fresh, gates={"strict"}) == ([], [])
        assert len(budget.compare(base, fresh)[0]) == 1

    def test_change_percentage_is_reported_against_the_baseline(self) -> None:
        violation = budget.Violation("c", "m", 100.0, 150.0, "strict", 0.0)
        assert violation.change_pct == pytest.approx(50.0)


class TestRegistry:
    def test_every_case_id_is_unique_and_tiers_are_labelled(self) -> None:
        cases = harness.registry()
        assert len(cases) == len({c.id for c in cases.values()})
        assert all(case.tier for case in cases.values())

    def test_warehouse_cases_state_why_they_are_not_measured(self) -> None:
        warehouse = harness.select(families=["warehouse_run"])
        assert warehouse
        assert all(case.skip_reason for case in warehouse)

    def test_the_ci_subset_excludes_every_warehouse_case(self) -> None:
        ci = harness.select(tags=["ci"])
        assert ci
        assert all(case.skip_reason is None for case in ci)

    def test_selection_by_family_and_id(self) -> None:
        assert {c.family for c in harness.select(families=["scheduler"])} == {"scheduler"}
        assert [c.id for c in harness.select(ids=["scheduler.10"])] == ["scheduler.10"]

    def test_flat_file_run_cases_lift_the_scan_caps_they_are_measuring(self) -> None:
        for case in harness.select(families=["flatfile_run"]):
            assert case.env["RUN_MAX_SCAN_BYTES"] == "0"
            assert case.env["RUN_MAX_SCAN_ROWS"] == "0"


class TestReportShape:
    def test_a_skipped_case_emits_a_row_naming_the_reason(self) -> None:
        case = harness.select(families=["warehouse_run"])[0]
        row = perf_baseline._skip_row(case, "abc123", "2026-01-01T00:00:00+00:00")
        assert row["status"] == "not_measured"
        assert row["value"] is None
        assert row["reason"] == case.skip_reason

    def test_rows_carry_the_identity_the_issue_asked_for(self) -> None:
        case = harness.select(ids=["scheduler.10"])[0]
        payload = {
            "case": case.id,
            "metrics": [{"name": "m", "value": 2.0, "unit": "ms", "gate": "observe"}],
        }
        rows = perf_baseline._rows_for_case(case, [payload, payload], "sha", "stamp")
        assert rows[0]["metric"] == "m"
        assert rows[0]["value"] == 2.0
        assert rows[0]["unit"] == "ms"
        assert rows[0]["tier"] == case.tier
        assert rows[0]["datasource"] == case.datasource
        assert rows[0]["git_sha"] == "sha"
        assert rows[0]["timestamp"] == "stamp"
        assert rows[0]["n"] == 2

    def test_csv_output_carries_a_header_and_one_line_per_row(self) -> None:
        text = perf_baseline._as_csv([_row("c", "m", 1.0, unit="s")])
        lines = text.strip().splitlines()
        assert lines[0].startswith("metric,value,unit")
        assert len(lines) == 2

    def test_the_rig_is_declared_not_prod_parity(self) -> None:
        assert harness.rig()["is_prod_parity"] is False

    def test_the_child_payload_carries_wall_rss_and_calibration(self) -> None:
        case = harness.Case(
            id="unit.probe",
            family="unit",
            datasource="none",
            tier="probe",
            fn=lambda: [harness.Metric("thing", 1.0, "count", "strict")],
        )
        names = {m["name"] for m in harness.execute(case)["metrics"]}
        assert {"thing", "wall_s", "wall_calibrated", "peak_rss_mib", "calibration_s"} <= names

    def test_prepare_runs_before_the_measured_work(self) -> None:
        order: list[str] = []

        def measured() -> list[harness.Metric]:
            order.append("fn")
            return []

        case = harness.Case(
            id="unit.prepare",
            family="unit",
            datasource="none",
            tier="probe",
            fn=measured,
            prepare=lambda: order.append("prepare"),
        )
        harness.execute(case)
        assert order == ["prepare", "fn"]

    def test_a_child_with_no_measurement_line_is_an_error_not_an_empty_result(self) -> None:
        with pytest.raises(RuntimeError, match="no measurement line"):
            harness.last_json_line("Traceback: boom\n", "some.case")


class TestScratchDatabaseGuard:
    def test_the_app_database_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERF_DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/dataq")
        with pytest.raises(cases_db.PerfDatabaseUnsetError, match="refusing"):
            cases_db.database_url()

    def test_an_unset_url_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PERF_DATABASE_URL", raising=False)
        with pytest.raises(cases_db.PerfDatabaseUnsetError, match="SCRATCH"):
            cases_db.database_url()

    def test_a_scratch_url_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "postgresql+psycopg2://u:p@localhost:5432/dataq_perf"
        monkeypatch.setenv("PERF_DATABASE_URL", url)
        assert cases_db.database_url() == url


class TestLocalStoreSeams:
    def test_the_seams_read_a_local_file_and_are_restored_afterwards(self, tmp_path: Path) -> None:
        from backend.app.datasources import flatfile

        target = tmp_path / "sample.csv"
        target.write_text("a,b\n1,2\n")
        original = flatfile.download_bytes

        with datagen.local_store() as counters:
            assert (
                flatfile.download_bytes(conn_type="s3", config={}, path=str(target), secret="x")
                == b"a,b\n1,2\n"
            )
            assert (
                flatfile.read_range(
                    conn_type="s3", config={}, path=str(target), secret="x", start=0, length=3
                )
                == b"a,b"
            )
            assert (
                flatfile.object_size(conn_type="s3", config={}, path=str(target), secret="x") == 8
            )
            assert (
                flatfile.file_stat(conn_type="s3", config={}, path=str(target), secret="x").size
                == 8
            )
            assert counters.calls == 4
            assert counters.bytes_read == 11

        assert flatfile.download_bytes is original

    def test_a_zero_length_range_costs_no_call(self, tmp_path: Path) -> None:
        from backend.app.datasources import flatfile

        target = tmp_path / "sample.csv"
        target.write_text("a\n")
        with datagen.local_store() as counters:
            assert (
                flatfile.read_range(
                    conn_type="s3", config={}, path=str(target), secret="x", start=0, length=0
                )
                == b""
            )
            assert counters.calls == 0

    def test_counters_reset_between_uses(self, tmp_path: Path) -> None:
        from backend.app.datasources import flatfile

        target = tmp_path / "sample.csv"
        target.write_text("a\n")
        with datagen.local_store() as first:
            flatfile.object_size(conn_type="s3", config={}, path=str(target), secret="x")
        with datagen.local_store() as second:
            pass
        assert first.calls == 1
        assert second.calls == 0


class TestCli:
    def test_check_defaults_to_the_ci_subset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_check(args: Any) -> int:
            captured["tag"] = args.tag
            return 0

        monkeypatch.setitem(perf_baseline.COMMANDS, "check", fake_check)
        assert perf_baseline.main(["check"]) == 0
        assert captured["tag"] == ["ci"]

    def test_an_explicit_selection_is_not_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setitem(
            perf_baseline.COMMANDS,
            "check",
            lambda args: (captured.update(tag=args.tag, family=args.family), 0)[1],
        )
        perf_baseline.main(["check", "--family", "db_read"])
        assert captured["tag"] is None
        assert captured["family"] == ["db_read"]

    def test_an_empty_selection_is_an_error_not_a_silent_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert perf_baseline.main(["run", "--case", "does.not.exist"]) == 2

    def test_the_committed_baseline_parses_and_carries_gated_metrics(self) -> None:
        baseline = json.loads(perf_baseline.BASELINE_PATH.read_text())
        gated = [r for r in baseline["rows"] if r.get("gate") in budget.TOLERANCES]
        assert gated, "a baseline with nothing gated cannot fail"
        assert baseline["rig"]["is_prod_parity"] is False
