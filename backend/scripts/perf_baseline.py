"""DataQ scale baseline — a parameterized benchmark, a committed baseline and a
regression budget.

Axes: datasource tier x volume tier x checks-per-suite. Every case drives the
REAL code path (`FlatFileCheckRunner`, the service-layer list queries, the
column profiler, the schedule dispatcher) and is measured in a fresh subprocess
so its peak RSS is attributable.

    # the flat-file + DB matrix (needs a scratch Postgres for the db families)
    export PERF_DATABASE_URL=postgresql+psycopg2://user:pw@localhost:5432/dataq_perf
    conda run -n dataq python -m backend.scripts.perf_baseline run --repeat 5 \
        --out /tmp/perf.json

    # the fast, deterministic subset — the regression budget
    conda run -n dataq python -m backend.scripts.perf_baseline check

    # refresh the committed baseline after an intentional change
    conda run -n dataq python -m backend.scripts.perf_baseline run --tag ci \
        --repeat 5 --out backend/scripts/perf/baseline.json

Warehouse tiers (Snowflake / Unity Catalog / Iceberg) drive the real runners and
emit an explicit ``not_measured`` row naming the environment variables they are
missing when no live warehouse is configured — a tier that is merely absent from
a result set reads as "nothing to report".

The Iceberg memory curve needs no warehouse: build its local fixtures once, then
run it under the deployed worker's limit, where a peak becomes a SIGKILL.

    python -m backend.scripts.perf_baseline gen-iceberg
    scripts/perf/run_in_rig.sh --build -- --tag iceberg_curve --out /perf-data/curve.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.scripts.perf import budget
from backend.scripts.perf.catalog import registry, select
from backend.scripts.perf.harness import (
    Case,
    CaseFailedError,
    execute,
    git_sha,
    now_iso,
    rig,
    run_in_subprocess,
    summarise,
)

BASELINE_PATH = Path(__file__).resolve().parent / "perf" / "baseline.json"


def _rows_for_case(
    case: Case, payloads: list[dict[str, Any]], sha: str, stamp: str
) -> list[dict[str, Any]]:
    by_metric: dict[str, list[float]] = defaultdict(list)
    for payload in payloads:
        for metric in payload["metrics"]:
            by_metric[metric["name"]].append(float(metric["value"]))
    units = {m["name"]: m["unit"] for p in payloads for m in p["metrics"]}
    gates = {m["name"]: m["gate"] for p in payloads for m in p["metrics"]}

    rows = []
    for name, values in by_metric.items():
        stats = summarise(values)
        rows.append(
            {
                "metric": name,
                "value": round(stats["value"], 6),
                "unit": units[name],
                "gate": gates[name],
                "tier": case.tier,
                "datasource": case.datasource,
                "family": case.family,
                "case": case.id,
                "status": "measured",
                "cov": round(stats["cov"], 4),
                "min": round(stats["min"], 6),
                "max": round(stats["max"], 6),
                "n": int(stats["n"]),
                "git_sha": sha,
                "timestamp": stamp,
            }
        )
    return rows


def _skip_row(case: Case, sha: str, stamp: str) -> dict[str, Any]:
    return {
        "metric": "not_measured",
        "value": None,
        "unit": "",
        "gate": "observe",
        "tier": case.tier,
        "datasource": case.datasource,
        "family": case.family,
        "case": case.id,
        "status": "not_measured",
        "reason": case.skip_reason,
        "git_sha": sha,
        "timestamp": stamp,
    }


def _killed_row(case: Case, exc: CaseFailedError, sha: str, stamp: str) -> dict[str, Any]:
    """A case whose child died is the ceiling being measured, not a broken run —
    so it is a ROW, and the rest of the curve still gets measured.
    """
    return {
        "metric": "killed",
        "value": None,
        "unit": "",
        "gate": "observe",
        "tier": case.tier,
        "datasource": case.datasource,
        "family": case.family,
        "case": case.id,
        "status": "killed",
        "returncode": exc.returncode,
        "signal": exc.signal,
        "oom_killed": exc.oom_killed,
        "timed_out": exc.timed_out,
        # A classification, never the child's own output: stderr can carry a
        # connection URL with a credential in it, and this row is written to a
        # file and pasted into a document.
        "reason": exc.describe(),
        "git_sha": sha,
        "timestamp": stamp,
    }


def run_cases(cases: list[Case], *, repeat: int) -> dict[str, Any]:
    sha, stamp = git_sha(), now_iso()
    rows: list[dict[str, Any]] = []
    for case in sorted(cases, key=lambda c: c.id):
        if case.skip_reason:
            rows.append(_skip_row(case, sha, stamp))
            print(f"SKIP {case.id}: {case.skip_reason}", file=sys.stderr)
            continue
        print(f"RUN  {case.id} x{repeat}", file=sys.stderr)
        try:
            payloads = [run_in_subprocess(case) for _ in range(repeat)]
        except CaseFailedError as exc:
            # Only a LIMIT becomes a row. An ordinary non-zero exit is a traceback
            # — a bad table name, an expired credential, a moved seam — and
            # recording that as a ceiling would turn a misconfiguration into a
            # finding, silently, with the run still reporting success.
            if not exc.is_ceiling:
                raise
            rows.append(_killed_row(case, exc, sha, stamp))
            print(f"KILLED {case.id}: {exc.describe()}", file=sys.stderr)
            continue
        rows.extend(_rows_for_case(case, payloads, sha, stamp))
    return {"generated_at": stamp, "git_sha": sha, "rig": rig(), "rows": rows}


def _write(report: dict[str, Any], out: str | None, fmt: str) -> None:
    text = json.dumps(report, indent=2) + "\n" if fmt == "json" else _as_csv(report["rows"])
    if out:
        Path(out).write_text(text)
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def _as_csv(rows: list[dict[str, Any]]) -> str:
    import io

    fields = [
        "metric",
        "value",
        "unit",
        "tier",
        "datasource",
        "family",
        "case",
        "gate",
        "status",
        "cov",
        "n",
        "git_sha",
        "timestamp",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _cmd_run(args: argparse.Namespace) -> int:
    cases = select(families=args.family, tags=args.tag, ids=args.case)
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2
    report = run_cases(cases, repeat=args.repeat)
    _write(report, args.out, args.format)
    return 0


def _cmd_exec(args: argparse.Namespace) -> int:
    case = registry()[args.case]
    print(json.dumps(execute(case)))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for case in sorted(registry().values(), key=lambda c: c.id):
        marker = "SKIP" if case.skip_reason else "    "
        print(f"{marker} {case.id:44s} {case.family:20s} {','.join(case.tags)}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text())
    cases = select(families=args.family, tags=args.tag, ids=args.case)
    fresh = run_cases(cases, repeat=args.repeat)
    if args.out:
        _write(fresh, args.out, "json")

    # A killed case carries the `observe` gate, so the budget skips it and would
    # otherwise print "budget OK" about a case that never produced a number.
    killed = [row["case"] for row in fresh["rows"] if row.get("status") == "killed"]
    if killed:
        print(f"BUDGET FAILED — {len(killed)} case(s) produced no measurement:")
        for row in fresh["rows"]:
            if row.get("status") == "killed":
                print(f"  {row['case']}: {row['reason']}")
        return 1

    gates = set(args.gate) if args.gate else None
    result = budget.compare(baseline["rows"], fresh["rows"], gates=gates)
    for note in result.notes:
        print(f"note: {note}")
    if result.ok:
        print(f"budget OK — {result.compared} metrics compared against {args.baseline}")
        return 0
    if result.violations:
        print(f"BUDGET FAILED — {len(result.violations)} metric(s) regressed:")
        for violation in result.violations:
            print(f"  {violation.describe()}")
    if result.unmatched:
        if args.allow_new:
            for entry in result.unmatched:
                print(f"note: {entry} (allowed by --allow-new)")
            if not result.violations:
                print(f"budget OK — {result.compared} metrics compared against {args.baseline}")
                return 0
        else:
            print(
                f"BUDGET FAILED — {len(result.unmatched)} gated metric(s) could not be compared "
                "(rename the baseline's case, or re-run with --allow-new):"
            )
            for entry in result.unmatched:
                print(f"  {entry}")
    return 1


def _cmd_seed_db(args: argparse.Namespace) -> int:
    from backend.scripts.perf import cases_db

    cases_db.seed(args.rows)
    return 0


def _cmd_gen_iceberg(args: argparse.Namespace) -> int:
    from backend.scripts.perf import datagen

    for rows in args.rows:
        print(datagen.build_iceberg_dataset(rows), file=sys.stderr)
    return 0


def _cmd_create_db(args: argparse.Namespace) -> int:
    from backend.scripts.perf import cases_db

    cases_db.create_database()
    return 0


def _cmd_reset_db(args: argparse.Namespace) -> int:
    from backend.scripts.perf import cases_db

    cases_db.reset()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="perf_baseline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_selection(target: argparse.ArgumentParser) -> None:
        target.add_argument("--family", action="append", help="limit to a case family")
        target.add_argument("--tag", action="append", help="limit to a tag (ci, full, warehouse)")
        target.add_argument("--case", action="append", help="limit to a case id")

    run = sub.add_parser("run", help="run cases and emit measurements")
    add_selection(run)
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--out")
    run.add_argument("--format", choices=("json", "csv"), default="json")

    execute_cmd = sub.add_parser("exec", help="run ONE case in this process (child entry point)")
    execute_cmd.add_argument("--case", required=True)

    sub.add_parser("list", help="list registered cases")

    check = sub.add_parser("check", help="run the budget against a committed baseline")
    add_selection(check)
    check.add_argument("--baseline", default=str(BASELINE_PATH))
    check.add_argument("--repeat", type=int, default=1)
    check.add_argument(
        "--allow-new",
        action="store_true",
        help="treat a gated metric with no baseline row as a note rather than a failure",
    )
    check.add_argument(
        "--gate",
        action="append",
        choices=sorted(budget.TOLERANCES),
        help="enforce only these gates (CI uses 'strict' — an RSS band is machine-specific)",
    )
    check.add_argument("--out")
    check.set_defaults(tag=None)

    seed = sub.add_parser("seed-db", help="seed the scratch database")
    seed.add_argument("--rows", type=int, default=100_000)

    gen = sub.add_parser(
        "gen-iceberg",
        help="build the local Iceberg curve fixtures under PERF_DATA_DIR (own process)",
    )
    gen.add_argument(
        "--rows",
        type=int,
        action="append",
        help="row count to build; repeatable. Default: every curve rung.",
    )

    sub.add_parser("create-db", help="create the scratch database named by PERF_DATABASE_URL")

    sub.add_parser("reset-db", help="truncate the scratch database")
    return parser


COMMANDS = {
    "run": _cmd_run,
    "exec": _cmd_exec,
    "list": _cmd_list,
    "check": _cmd_check,
    "gen-iceberg": _cmd_gen_iceberg,
    "seed-db": _cmd_seed_db,
    "create-db": _cmd_create_db,
    "reset-db": _cmd_reset_db,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check" and not (args.tag or args.family or args.case):
        args.tag = ["ci"]
    if args.command == "gen-iceberg" and not args.rows:
        from backend.scripts.perf.cases_warehouse import ICEBERG_CURVE_ROWS

        args.rows = list(ICEBERG_CURVE_ROWS)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
