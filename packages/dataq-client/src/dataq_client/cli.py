"""The ``dataq`` command line: run a suite or a pipeline gate and exit by the result; export,
import, validate and apply suite documents.

Exit codes, so a CI step or a scheduler task needs no Python::

    0  the run finished and nothing failed (``gate``: passed)
    1  the worst result was a warning
    2  a check failed (fail or critical) (``gate``: failed at or above ``--fail-on``)
       (``validate``: the document is invalid; ``drift``: the suite differs from the document)
    3  the run itself did not complete (failed, cancelled) or a check could not be evaluated
       (``gate``: error)
    4  a client, auth or transport problem, or a timeout — DataQ's verdict is unknown

The token comes from ``DATAQ_PAT`` only (a flag would land in shell history); the URL from
``--url`` or ``DATAQ_URL``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dataq_client.client import (
    DataQClient,
    DataQError,
    GateOutcome,
    GateTimeoutError,
    RunOutcome,
    RunTimeoutError,
)

EXIT_OK, EXIT_WARN, EXIT_FAIL, EXIT_RUN_ERROR, EXIT_CLIENT_ERROR = 0, 1, 2, 3, 4


def exit_code(outcome: RunOutcome) -> int:
    """The exit code for a finished run. The run's own failure outranks any check verdict."""
    if outcome.status in ("failed", "cancelled") or outcome.checks_errored:
        return EXIT_RUN_ERROR
    if outcome.worst_severity in ("fail", "critical"):
        return EXIT_FAIL
    if outcome.worst_severity == "warn":
        return EXIT_WARN
    return EXIT_OK


_GATE_EXIT = {"passed": EXIT_OK, "failed": EXIT_FAIL, "error": EXIT_RUN_ERROR}


def _print_gate(outcome: GateOutcome, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(outcome)))
        return
    print(f"gate {outcome.triggered_by}: {outcome.state} (fail on {outcome.fail_on})")
    for suite in outcome.suites:
        print(
            f"  suite {suite['suite_id']}: {suite['state']}"
            f" · worst severity {suite.get('worst_severity') or 'none'}"
            f" · {suite.get('checks_passed', 0)}/{suite.get('checks_total', 0)} passed"
        )


def _print_outcome(outcome: RunOutcome, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    **asdict(outcome),
                    "run_id": str(outcome.run_id),
                    "suite_id": str(outcome.suite_id),
                }
            )
        )
        return
    print(
        f"run {outcome.run_id}: {outcome.status}"
        f" · worst severity {outcome.worst_severity or 'none'}"
        f" · {outcome.checks_passed}/{outcome.checks_total} passed"
        + (f" · {outcome.checks_errored} errored" if outcome.checks_errored else "")
        + (f" · {outcome.failure_reason}" if outcome.failure_reason else "")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataq", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", help="the DataQ host (default: $DATAQ_URL)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="queue a suite run, optionally wait and gate on it")
    run.add_argument("suite_id")
    run.add_argument("--wait", action="store_true", help="wait for the run and exit by its result")
    run.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait (default 1800)")
    run.add_argument("--interval", type=float, default=5.0, help="poll interval, at least 5 s")
    run.add_argument("--json", action="store_true", help="print the outcome as JSON")

    wait = commands.add_parser("wait", help="wait for an existing run and exit by its result")
    wait.add_argument("run_id")
    wait.add_argument("--timeout", type=float, default=1800.0)
    wait.add_argument("--interval", type=float, default=5.0)
    wait.add_argument("--json", action="store_true")

    gate = commands.add_parser(
        "gate",
        help="ask whether a pipeline may continue: run its bound suites and exit by the verdict",
    )
    gate.add_argument("--provider", required=True, choices=("adf", "airflow", "dbt"))
    gate.add_argument("--pipeline", required=True, help="the bound pipeline, DAG or dbt job id")
    gate.add_argument("--env", required=True, choices=("dev", "qa", "uat", "prod"))
    gate.add_argument("--run-id", required=True, help="this pipeline run's id (the dedup key)")
    gate.add_argument("--fail-on", default="fail", choices=("warn", "fail", "critical"))
    gate.add_argument(
        "--no-trigger",
        action="store_true",
        help="only report on runs already triggered for this pipeline run (needs view, not edit)",
    )
    gate.add_argument("--timeout", type=float, default=1800.0)
    gate.add_argument("--json", action="store_true")

    export = commands.add_parser("export", help="write a suite's document to a file or stdout")
    export.add_argument("suite_id")
    export.add_argument("-o", "--output", help="file to write (default: stdout)")
    export.add_argument(
        "--format",
        choices=("json", "yaml"),
        help="default: yaml when --output ends in .yaml/.yml, else json",
    )

    imp = commands.add_parser("import", help="create a NEW suite from a document (JSON or YAML)")
    imp.add_argument("file")
    imp.add_argument("--connection", required=True, help="the connection the new suite runs on")

    validate = commands.add_parser(
        "validate",
        help="check a suite document against a connection; creates nothing. Exit 2 if invalid",
    )
    validate.add_argument("file")
    validate.add_argument("--connection", required=True, help="the connection it would run on")
    validate.add_argument("--json", action="store_true")

    for name, text in (
        ("apply", "bring an existing suite in line with a document (checks matched by name)"),
        ("drift", "report how a suite differs from a document; writes nothing. Exit 2 on drift"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("file")
        sub.add_argument("--suite", required=True, help="the suite to compare or change")
        sub.add_argument(
            "--prune",
            action="store_true",
            help="also delete (apply) or count as drift (drift) checks the document does not name",
        )
        sub.add_argument("--json", action="store_true")
    return parser


def _read_document(path: str) -> dict[str, Any] | str:
    """A .yaml/.yml file is sent as text for the server to parse; anything else is JSON."""
    text = Path(path).read_text()
    if path.lower().endswith((".yaml", ".yml")):
        return text
    document: dict[str, Any] = json.loads(text)
    return document


def _print_plan(plan: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(plan))
        return
    for field in plan["suite_fields"]:
        print(f"  suite {field}: update")
    for check in plan["checks"]:
        if check["action"] == "unchanged":
            continue
        detail = f" ({', '.join(check['fields'])})" if check["fields"] else ""
        print(f"  {check['action']:<7} {check['name']}{detail}")
    for name in plan["unmanaged"]:
        print(f"  not in the document, left alone: {name}")
    counts = {
        a: sum(c["action"] == a for c in plan["checks"]) for a in ("create", "update", "delete")
    }
    verb = "would change" if plan["dry_run"] else "changed"
    if plan["changed"]:
        print(
            f"{verb}: {counts['create']} created, {counts['update']} updated, "
            f"{counts['delete']} deleted"
        )
    else:
        print("no drift" if plan["dry_run"] else "already up to date")


def _run(args: argparse.Namespace, client: DataQClient) -> int:
    if args.command == "run":
        outcome = client.trigger_run(args.suite_id)
        if not args.wait:
            _print_outcome(outcome, as_json=args.json)
            return EXIT_OK
        outcome = client.wait_for_run(outcome.run_id, timeout=args.timeout, interval=args.interval)
        _print_outcome(outcome, as_json=args.json)
        return exit_code(outcome)
    if args.command == "wait":
        outcome = client.wait_for_run(args.run_id, timeout=args.timeout, interval=args.interval)
        _print_outcome(outcome, as_json=args.json)
        return exit_code(outcome)
    if args.command == "gate":
        verdict = client.gate(
            provider=args.provider,
            pipeline_or_dag_id=args.pipeline,
            env=args.env,
            provider_run_id=args.run_id,
            fail_on=args.fail_on,
            trigger=not args.no_trigger,
            timeout=args.timeout,
        )
        _print_gate(verdict, as_json=args.json)
        return _GATE_EXIT[verdict.state]
    if args.command == "export":
        as_yaml = args.format == "yaml" or (
            args.format is None and (args.output or "").lower().endswith((".yaml", ".yml"))
        )
        if as_yaml:
            text = client.export_suite_yaml(args.suite_id)
        else:
            text = json.dumps(client.export_suite(args.suite_id), indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(text)
        else:
            sys.stdout.write(text)
        return EXIT_OK
    if args.command == "validate":
        result = client.validate_suite(_read_document(args.file), args.connection)
        if args.json:
            print(json.dumps(result))
        else:
            for problem in result["problems"]:
                name = f" ({problem['check_name']})" if problem.get("check_name") else ""
                print(f"  {problem['location']}{name}: {problem['message']}")
            print(
                f"valid: {result['check_count']} checks"
                if result["valid"]
                else f"invalid: {len(result['problems'])} problem(s)"
            )
        return EXIT_OK if result["valid"] else EXIT_FAIL
    if args.command in ("apply", "drift"):
        dry_run = args.command == "drift"
        plan = client.apply_suite(
            args.suite, _read_document(args.file), prune=args.prune, dry_run=dry_run
        )
        _print_plan(plan, as_json=args.json)
        return EXIT_FAIL if dry_run and plan["changed"] else EXIT_OK
    suite = client.import_suite(_read_document(args.file), args.connection)
    print(f"imported suite {suite.id}: {suite.name}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with DataQClient(args.url) as client:
            return _run(args, client)
    except GateTimeoutError as exc:
        print(f"dataq: {exc}", file=sys.stderr)
        return EXIT_CLIENT_ERROR
    except RunTimeoutError as exc:
        print(f"dataq: {exc} — it is still running on the server", file=sys.stderr)
        return EXIT_CLIENT_ERROR
    except DataQError as exc:
        print(f"dataq: {exc}", file=sys.stderr)
        return EXIT_CLIENT_ERROR
    except (ValueError, OSError) as exc:
        print(f"dataq: {exc}", file=sys.stderr)
        return EXIT_CLIENT_ERROR
    except Exception as exc:  # a transport failure (httpx) or anything unexpected
        print(f"dataq: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CLIENT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
