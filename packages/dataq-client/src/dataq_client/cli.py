"""The ``dataq`` command line: run a suite and gate on it, export and import suites.

Exit codes, so a CI step or a scheduler task needs no Python::

    0  the run finished and nothing failed
    1  the worst result was a warning
    2  a check failed (fail or critical)
    3  the run itself did not complete (failed, cancelled) or a check could not be evaluated
    4  a client, auth or transport problem — DataQ's verdict is unknown

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

from dataq_client.client import DataQClient, DataQError, RunOutcome, RunTimeoutError

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

    export = commands.add_parser("export", help="write a suite's document to a file or stdout")
    export.add_argument("suite_id")
    export.add_argument("-o", "--output", help="file to write (default: stdout)")

    imp = commands.add_parser("import", help="create a suite from an exported document")
    imp.add_argument("file")
    imp.add_argument("--connection", required=True, help="the connection the new suite runs on")
    return parser


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
    if args.command == "export":
        text = json.dumps(client.export_suite(args.suite_id), indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(text)
        else:
            sys.stdout.write(text)
        return EXIT_OK
    document: dict[str, Any] = json.loads(Path(args.file).read_text())
    suite = client.import_suite(document, args.connection)
    print(f"imported suite {suite.id}: {suite.name}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with DataQClient(args.url) as client:
            return _run(args, client)
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
