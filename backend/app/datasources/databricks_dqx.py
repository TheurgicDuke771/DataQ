"""Databricks Labs DQX — the Unity Catalog platform-native check engine (ADR 0036 §6).

DQX is published under the proprietary Databricks License, so it is never a DataQ dependency
(ADR 0031, CONTRIBUTING rule 40): DataQ submits a serverless job to the connection's own
workspace, where the notebook below installs DQX, and reads back failing-row counts only.

DQX evaluates a bare string argument as a Spark SQL expression (`check_funcs.get_limit_expr`),
so rules are built here from a closed vocabulary with typed arguments: identifiers from the
allowlist, string values as escaped SQL literals, limits as numbers or DataQ-validated ISO
dates. A user-supplied string never reaches DQX unquoted.

A check in ``stream`` mode reads the table as a Delta stream (``availableNow``) and evaluates only
the rows appended since its last persisted result, which records the version to resume from.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from typing import Any

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.sql import is_sql_identifier
from backend.app.services.failure_classifier import safe_failure_reason

log = get_logger(__name__)

DQX_ENGINE = "dqx"
DQX_VERSION = "0.16.0"
DQX_KINDS = ("expectation",)

_COLUMN_ONLY = frozenset({"column"})
#: expectation type → (DQX function, the config keys it takes).
DQX_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    "dqx:is_not_null": ("is_not_null", _COLUMN_ONLY),
    "dqx:is_not_empty": ("is_not_empty", _COLUMN_ONLY),
    "dqx:is_not_null_and_not_empty": ("is_not_null_and_not_empty", _COLUMN_ONLY),
    "dqx:is_in_list": ("is_in_list", frozenset({"column", "allowed"})),
    "dqx:is_in_range": ("is_in_range", frozenset({"column", "min_limit", "max_limit"})),
    "dqx:regex_match": ("regex_match", frozenset({"column", "regex"})),
    "dqx:is_not_less_than": ("is_not_less_than", frozenset({"column", "limit"})),
    "dqx:is_not_greater_than": ("is_not_greater_than", frozenset({"column", "limit"})),
}
DQX_EXPECTATION_TYPES = tuple(DQX_TYPES)

_MAX_ALLOWED_VALUES = 500
_MAX_STRING_CHARS = 1_000
_JOB_TIMEOUT_SECONDS = 1_800
_POLL_SECONDS = 10
_TERMINAL = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})
#: Consecutive transient poll failures (429, 5xx, connection) tolerated before giving up.
_POLL_RETRIES = 5

#: Optional on every dqx type: ``snapshot`` (default) reads the whole table; ``stream`` reads it as
#: a Delta stream and evaluates only rows appended since the check's last persisted result.
DQX_MODE_KEY = "mode"
DQX_MODES = ("snapshot", "stream")

#: The notebook submitted to the workspace. Pinned DQX; an invalid rule errors only itself; returns
#: counts, never row values. Snapshot rules share one Spark pass over the table. Stream rules share
#: one ``availableNow`` Delta stream per resume version, with a throwaway checkpoint in the
#: connection's volume: DataQ, not the checkpoint, holds the resume version, so a job whose result
#: is never persisted re-reads the same rows next run instead of skipping them. A failed stream
#: fails the whole job (Databricks fails the command even when the error is caught), so every
#: predictable stream error is avoided up front: a rule is pre-flighted on an empty read, and a
#: resume version past the latest commit starts no stream at all.
_NOTEBOOK_TEMPLATE = """# Databricks notebook source
# MAGIC %pip install databricks-labs-dqx==@DQX_VERSION@
# COMMAND ----------
dbutils.library.restartPython()
# COMMAND ----------
import json
import uuid
from pyspark.sql import functions as F
from databricks.labs.dqx.engine import DQEngine
from databricks.sdk import WorkspaceClient

with open("/Workspace" + dbutils.widgets.get("spec_path")) as spec_file:
    spec = json.load(spec_file)
engine = DQEngine(WorkspaceClient())
table = spec["table"]
results = {}


def validated(checks):
    valid = []
    for name, check in checks.items():
        rule = {"name": name, "criticality": "error", "check": check}
        status = engine.validate_checks([rule])
        if status.has_errors:
            results[name] = {"error": "invalid rule: " + "; ".join(status.errors)[:300]}
        else:
            valid.append(rule)
    return valid


def run_error(exc):
    return {"error": type(exc).__name__ + ": " + str(exc)[:300]}


rows = None
snapshot = validated(spec["checks"])
if snapshot:
    df = spark.table(table)
    rows = df.count()

    def failing_counts(rules):
        out = engine.apply_checks_by_metadata(df, rules)
        return {
            r["name"]: r["count"]
            for r in out.select(F.explode("_errors").alias("e")).groupBy("e.name").count().collect()
        }

    try:
        counts = failing_counts(snapshot)
    except Exception:
        # One rule that fails at run time must not error its siblings: apply each alone.
        counts = {}
        for rule in snapshot:
            try:
                counts.update(failing_counts([rule]))
                counts.setdefault(rule["name"], 0)
            except Exception as exc:
                results[rule["name"]] = run_error(exc)
    for rule in snapshot:
        if rule["name"] not in results:
            results[rule["name"]] = {"failing": int(counts.get(rule["name"], 0))}

REWRITES = {
    "DELETE", "UPDATE", "MERGE", "TRUNCATE", "RESTORE", "REPLACE TABLE",
    "REPLACE TABLE AS SELECT", "CREATE OR REPLACE TABLE", "CREATE OR REPLACE TABLE AS SELECT",
}


def rewrites(entry):
    params = entry["operationParameters"] or {}
    return entry["operation"] in REWRITES or (
        entry["operation"] == "WRITE" and params.get("mode") == "Overwrite"
    )


def history():
    return spark.sql(f"DESCRIBE HISTORY {table}").select(
        "version", "operation", "operationParameters"
    ).collect()


def preflight(rules):
    empty = spark.read.table(table).limit(0)
    ok = []
    for rule in rules:
        try:
            engine.apply_checks_by_metadata(empty, [rule]).limit(0).collect()
            ok.append(rule)
        except Exception as exc:
            results[rule["name"]] = run_error(exc)
    return ok


def failing_metric(name):
    hit = F.exists(F.coalesce(F.col("_errors"), F.array()), lambda e: e["name"] == F.lit(name))
    return F.sum(F.when(hit, 1).otherwise(0)).alias(name)


def metric_values(progress):
    observed = (progress.get("observedMetrics") or {}).get("dataq")
    if isinstance(observed, dict) and "values" in observed:
        names = [f["name"] for f in observed["schema"]["fields"]]
        return dict(zip(names, observed["values"]))
    return observed


if spec["streams"]:
    table_id = spark.sql(f"DESCRIBE DETAIL {table}").first()["id"]
    versions = [entry["version"] for entry in history()]
    latest, earliest = max(versions), min(versions)
for group in spec["streams"]:
    start, restarted = group["starting_version"], None
    if start is not None and group["table_id"] != table_id:
        start, restarted = None, "table_replaced"
    elif start is not None and start < earliest:
        start, restarted = None, "history_expired"
    rules = preflight(validated(group["checks"]))
    if not rules:
        continue
    state = {"table_id": table_id, "from_version": start, "restarted": restarted}
    totals = {"rows": 0, **{rule["name"]: 0 for rule in rules}}
    end, batches = None, set()
    if start is None or start <= latest:
        checkpoint = spec["checkpoint_root"] + "/dataq_dqx/" + uuid.uuid4().hex
        reader = spark.readStream.option("skipChangeCommits", "true").option(
            "maxFilesPerTrigger", "100000"
        )
        if start is not None:
            reader = reader.option("startingVersion", start)
        checked = engine.apply_checks_by_metadata(reader.table(table), rules)
        observed = checked.observe(
            "dataq", F.count(F.lit(1)).alias("rows"), *[failing_metric(r["name"]) for r in rules]
        )
        query = (
            observed.writeStream.format("noop")
            .trigger(availableNow=True)
            .option("checkpointLocation", checkpoint)
            .start()
        )
        try:
            query.awaitTermination()
            progress = query.recentProgress
        finally:
            try:
                dbutils.fs.rm(checkpoint, True)
            except Exception:
                pass
        incomplete = False
        for p in progress:
            batches.add(p["batchId"])
            if p.get("numInputRows"):
                values = metric_values(p)
                if not isinstance(values, dict):
                    incomplete = True
                    continue
                for key in totals:
                    totals[key] += int(values.get(key) or 0)
            offset = p["sources"][0].get("endOffset")
            if offset:
                end = json.loads(offset)
        if incomplete or (batches and batches != set(range(max(batches) + 1))):
            # Spark keeps a bounded progress history: a missing batch would under-count.
            for rule in rules:
                results[rule["name"]] = {"error": "incomplete micro-batch metrics"}
            continue
    if end is None:
        next_version = start if start is not None and start > latest else latest + 1
    elif end.get("index", -1) == -1:
        next_version = int(end["reservoirVersion"])
    else:
        next_version = int(end["reservoirVersion"]) + 1
    low = start if start is not None else latest + 1
    state.update(
        next_version=next_version,
        batches=len(batches),
        change_commits_skipped=sum(
            1 for e in history() if low <= e["version"] < next_version and rewrites(e)
        ),
    )
    for rule in rules:
        results[rule["name"]] = {
            "failing": int(totals[rule["name"]]), "rows": int(totals["rows"]), "stream": state,
        }
dbutils.notebook.exit(json.dumps({"rows": rows, "results": results, "dqx": "@DQX_VERSION@"}))
"""
RUNNER_NOTEBOOK = _NOTEBOOK_TEMPLATE.replace("@DQX_VERSION@", DQX_VERSION)


class DqxConfigError(DataQError):
    status_code = 422
    code = "check_config_invalid"


def _column(config: dict[str, Any]) -> str:
    column = config.get("column")
    if not is_sql_identifier(column):
        raise DqxConfigError("a dqx check needs a valid 'column' identifier in config")
    return f"`{column}`"


def _literal(value: Any, *, what: str) -> Any:
    """A value DQX will read as a literal: numbers as JSON numbers, strings SQL-quoted."""
    if isinstance(value, bool) or value is None:
        raise DqxConfigError(f"{what} must be a number or a string")
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and len(value) <= _MAX_STRING_CHARS:
        # Spark processes backslash escapes inside string literals and has no `''` escape (it
        # reads 'O''Brien' as 'O' 'Brien' = OBrien): backslashes are doubled, then quotes
        # become \', so `x\' || col || \'` stays a literal and O'Brien matches O'Brien.
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    raise DqxConfigError(f"{what} must be a number or a string of at most {_MAX_STRING_CHARS}")


def _limit(value: Any, *, what: str) -> Any:
    """A range limit: a number, or an ISO date/datetime validated here (DQX parses the rest as
    a SQL expression)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            dt.datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return value
    raise DqxConfigError(f"{what} must be a number or an ISO-8601 date/datetime")


def build_dqx_rule(expectation_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """The DQX ``check`` block for one DataQ check — validated, never passing a raw string."""
    entry = DQX_TYPES.get(expectation_type)
    if entry is None:
        raise DqxConfigError(f"expectation_type {expectation_type!r} is not a dqx check")
    function, keys = entry
    dqx_mode(config)
    given = set(config) - {DQX_MODE_KEY}
    unknown = sorted(given - keys)
    missing = sorted(keys - given)
    if unknown or missing:
        raise DqxConfigError(
            f"a {expectation_type} config takes exactly {sorted(keys)}; "
            f"unknown: {unknown or 'none'}, missing: {missing or 'none'}"
        )
    arguments: dict[str, Any] = {"column": _column(config)}
    if function == "is_in_list":
        allowed = config["allowed"]
        if not isinstance(allowed, list) or not 1 <= len(allowed) <= _MAX_ALLOWED_VALUES:
            raise DqxConfigError(f"'allowed' must be a list of 1 to {_MAX_ALLOWED_VALUES} values")
        arguments["allowed"] = [_literal(v, what="each allowed value") for v in allowed]
    elif function == "is_in_range":
        arguments["min_limit"] = _limit(config["min_limit"], what="min_limit")
        arguments["max_limit"] = _limit(config["max_limit"], what="max_limit")
    elif function == "regex_match":
        regex = config["regex"]
        if not isinstance(regex, str) or not 0 < len(regex) <= _MAX_STRING_CHARS:
            raise DqxConfigError(
                f"'regex' must be a non-empty string of at most {_MAX_STRING_CHARS}"
            )
        # `regex_match` hands this to `rlike` as a string, not through `F.expr`.
        arguments["regex"] = regex
    elif function in {"is_not_less_than", "is_not_greater_than"}:
        arguments["limit"] = _limit(config["limit"], what="limit")
    return {"function": function, "arguments": arguments}


def dqx_mode(config: dict[str, Any]) -> str:
    mode = config.get(DQX_MODE_KEY, "snapshot")
    if mode not in DQX_MODES:
        raise DqxConfigError(f"'{DQX_MODE_KEY}' must be one of {list(DQX_MODES)}")
    return str(mode)


def checkpoint_root(volume: str) -> str:
    """``catalog.schema.volume`` → its ``/Volumes`` path, every part an allowlisted identifier."""
    parts = volume.split(".")
    if len(parts) != 3 or not all(is_sql_identifier(part) for part in parts):
        raise ValueError("the DQX checkpoint volume must be catalog.schema.volume")
    return "/Volumes/" + "/".join(parts)


def _resume_point(previous: Any, table_key: str) -> tuple[int | None, str | None]:
    """Where a stream check resumes: the version its last evaluated result recorded, if that
    result was a stream read of this same table; otherwise the start of the table."""
    state = previous.get("stream") if isinstance(previous, dict) else None
    if not isinstance(state, dict) or state.get("table") != table_key:
        return None, None
    version, table_id = state.get("next_version"), state.get("table_id")
    if (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 0
        and isinstance(table_id, str)
        and 0 < len(table_id) <= 64
    ):
        return version, table_id
    return None, None


def _stream_state(raw: Any, table_key: str) -> dict[str, Any] | None:
    """The workspace's stream report, kept only when it is well-formed: its ``next_version``
    becomes the next run's resume point."""
    if not isinstance(raw, dict):
        return None
    version, table_id = raw.get("next_version"), raw.get("table_id")
    if not (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 0
        and isinstance(table_id, str)
        and 0 < len(table_id) <= 64
    ):
        return None
    start = raw.get("from_version")
    restarted = raw.get("restarted")
    return {
        "table": table_key,
        "table_id": table_id,
        "from_version": start if isinstance(start, int) and not isinstance(start, bool) else None,
        "next_version": version,
        "restarted": restarted if restarted in ("table_replaced", "history_expired") else None,
        "batches": _count(raw.get("batches")),
        "change_commits_skipped": _count(raw.get("change_commits_skipped")),
    }


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def qualified_table(*, catalog: str, schema: str, table: str) -> str:
    for part in (catalog, schema, table):
        if not is_sql_identifier(part):
            raise DqxConfigError(
                "a dqx run needs a catalog.schema.table target of plain identifiers"
            )
    return f"`{catalog}`.`{schema}`.`{table}`"


def _error_outcome(expectation_type: str, message: str) -> CheckOutcome:
    return CheckOutcome(
        expectation_type=expectation_type,
        success=False,
        errored=True,
        error_message=message,
        expected_value={"engine": DQX_ENGINE},
    )


def outcomes_from_payload(
    specs: list[tuple[str, dict[str, Any]]],
    payload: dict[str, Any],
    *,
    table_key: str | None = None,
) -> list[CheckOutcome]:
    """Map the notebook's ``{rows, results: {name: {failing|error}}}`` back onto ``specs``.

    A stream result carries its own ``rows`` (the rows appended since the last evaluation) and a
    ``stream`` report; a stream check whose report is malformed errors rather than persisting a
    resume point DataQ cannot trust."""
    rows = payload.get("rows")
    raw_results = payload.get("results")
    results: dict[str, Any] = raw_results if isinstance(raw_results, dict) else {}
    outcomes = []
    for index, (expectation_type, config) in enumerate(specs):
        result = results.get(_rule_name(index))
        if not isinstance(result, dict):
            outcomes.append(_error_outcome(expectation_type, "the dqx job returned no result"))
            continue
        if "error" in result:
            # Workspace-side text; classified, never stored raw.
            reason = safe_failure_reason(RuntimeError(str(result["error"])))
            outcomes.append(_error_outcome(expectation_type, f"dqx rejected the rule: {reason}"))
            continue
        failing = result.get("failing")
        if not isinstance(failing, int) or isinstance(failing, bool) or failing < 0:
            outcomes.append(_error_outcome(expectation_type, "the dqx job returned a bad count"))
            continue
        observed: dict[str, Any] = {"failing_rows": failing, "rows": rows}
        expected = {"engine": DQX_ENGINE, "function": DQX_TYPES[expectation_type][0], **config}
        if dqx_mode(config) == "stream":
            state = _stream_state(result.get("stream"), table_key or "")
            if state is None or _count(result.get("rows")) is None:
                outcomes.append(
                    _error_outcome(expectation_type, "the dqx job returned a bad stream report")
                )
                continue
            observed = {
                "failing_rows": failing,
                "rows": _count(result.get("rows")),
                "stream": state,
            }
            if observed["rows"] == 0:
                # Nothing appended since the last evaluation: a skip, not a pass. A skip is not
                # a resume point, so the next run re-reads from the same version.
                outcomes.append(
                    CheckOutcome(
                        expectation_type=expectation_type,
                        success=True,
                        skipped=True,
                        observed_value={**observed, "reason": "no rows appended since last run"},
                        expected_value=expected,
                    )
                )
                continue
        outcomes.append(
            CheckOutcome(
                expectation_type=expectation_type,
                success=failing == 0,
                metric_value=float(failing),
                observed_value=observed,
                expected_value=expected,
            )
        )
    return outcomes


def _rule_name(index: int) -> str:
    return f"dataq_{index}"


def _transient(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class DqxJobs:
    """The Jobs and Workspace REST calls one DQX batch needs (live seam)."""

    def __init__(self, *, workspace_url: str, token: str, client: Any = None) -> None:
        self._base = workspace_url.rstrip("/")
        self._token = token
        self._client = client

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {self._token}"}
        if self._client is not None:
            response = self._client.request(
                method, f"{self._base}{path}", json=body, headers=headers
            )
        else:
            with httpx.Client(timeout=60) as client:
                response = client.request(method, f"{self._base}{path}", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def notebook_path(self) -> str:
        user = self._call("GET", "/api/2.0/preview/scim/v2/Me").get("userName")
        if not isinstance(user, str) or not user:
            raise RuntimeError("the workspace did not name the token's user")
        return f"/Users/{user}/.dataq/dqx_runner_{DQX_VERSION.replace('.', '_')}"

    def upload(self, path: str) -> None:
        import base64

        self._call("POST", "/api/2.0/workspace/mkdirs", {"path": path.rsplit("/", 1)[0]})
        self._call(
            "POST",
            "/api/2.0/workspace/import",
            {
                "path": path,
                "format": "SOURCE",
                "language": "PYTHON",
                "content": base64.b64encode(RUNNER_NOTEBOOK.encode()).decode(),
                "overwrite": True,
            },
        )

    def upload_spec(self, notebook_path: str, spec: dict[str, Any]) -> str:
        """The run's rules as a workspace file: a notebook parameter has a size limit a long
        allowed-values list can exceed, a workspace file does not."""
        import base64
        import uuid

        path = f"{notebook_path.rsplit('/', 1)[0]}/specs/{uuid.uuid4().hex}.json"
        self._call("POST", "/api/2.0/workspace/mkdirs", {"path": path.rsplit("/", 1)[0]})
        self._call(
            "POST",
            "/api/2.0/workspace/import",
            {
                "path": path,
                "format": "AUTO",
                "content": base64.b64encode(json.dumps(spec).encode()).decode(),
                "overwrite": True,
            },
        )
        return path

    def delete(self, path: str) -> None:
        self._call("POST", "/api/2.0/workspace/delete", {"path": path})

    def cancel(self, run_id: int) -> None:
        self._call("POST", "/api/2.2/jobs/runs/cancel", {"run_id": run_id})

    def submit(self, path: str, spec_path: str) -> int:
        run = self._call(
            "POST",
            "/api/2.2/jobs/runs/submit",
            {
                "run_name": "dataq-dqx",
                "timeout_seconds": _JOB_TIMEOUT_SECONDS,
                "tasks": [
                    {
                        "task_key": "dqx",
                        "notebook_task": {
                            "notebook_path": path,
                            "base_parameters": {"spec_path": spec_path},
                        },
                    }
                ],
            },
        )
        run_id = run.get("run_id")
        if not isinstance(run_id, int):
            raise RuntimeError("the workspace did not return a run id")
        return run_id

    def wait(self, run_id: int, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        deadline = time.monotonic() + _JOB_TIMEOUT_SECONDS + 120
        failures = 0
        while True:
            try:
                run = self._call("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
            except Exception as exc:
                # One throttled or failed poll must not fail a run the workspace is still
                # executing — only a sustained outage does.
                failures += 1
                if failures > _POLL_RETRIES or not _transient(exc):
                    raise
                sleep(_POLL_SECONDS)
                continue
            failures = 0
            state = run.get("state") or {}
            if state.get("life_cycle_state") in _TERMINAL:
                if state.get("result_state") != "SUCCESS":
                    raise RuntimeError(f"the dqx job ended {state.get('result_state')}")
                task_run = (run.get("tasks") or [{}])[0].get("run_id")
                output = self._call("GET", f"/api/2.2/jobs/runs/get-output?run_id={task_run}")
                result = (output.get("notebook_output") or {}).get("result")
                if not isinstance(result, str):
                    raise RuntimeError("the dqx job returned no output")
                payload = json.loads(result)
                if not isinstance(payload, dict):
                    raise RuntimeError("the dqx job returned a non-object")
                return payload
            if time.monotonic() > deadline:
                raise TimeoutError("the dqx job did not finish in time")
            sleep(_POLL_SECONDS)


def run_dqx_batch(
    jobs: DqxJobs,
    specs: list[tuple[str, dict[str, Any]]],
    *,
    catalog: str,
    schema: str,
    table: str,
    previous: list[dict[str, Any] | None] | None = None,
    checkpoint_volume: str | None = None,
) -> list[CheckOutcome]:
    """Evaluate every dqx check of a run in ONE workspace job; never raises.

    A rule DataQ itself refuses errors only that check; a job-level failure errors the whole
    batch with a classified reason. ``previous`` is each check's last evaluated
    ``observed_value``: a stream check resumes from the version it recorded.
    """
    table_key = f"{catalog}.{schema}.{table}"
    rules: dict[str, dict[str, Any]] = {}
    streams: dict[tuple[int | None, str | None], dict[str, dict[str, Any]]] = {}
    refused: dict[int, CheckOutcome] = {}
    for index, (expectation_type, config) in enumerate(specs):
        try:
            rule = build_dqx_rule(expectation_type, config)
            if dqx_mode(config) == "stream":
                if checkpoint_volume is None:
                    raise DqxConfigError(
                        "a stream-mode dqx check needs a DQX checkpoint volume on the connection"
                    )
                prior = previous[index] if previous and index < len(previous) else None
                streams.setdefault(_resume_point(prior, table_key), {})[_rule_name(index)] = rule
            else:
                rules[_rule_name(index)] = rule
        except DqxConfigError as exc:
            refused[index] = _error_outcome(expectation_type, exc.message)
    if not rules and not streams:
        return [refused[i] for i in range(len(specs))]
    run_id: int | None = None
    spec_path: str | None = None
    try:
        spec = {
            "table": qualified_table(catalog=catalog, schema=schema, table=table),
            "checks": rules,
            "streams": [
                {"starting_version": start, "table_id": table_id, "checks": group}
                for (start, table_id), group in streams.items()
            ],
            "checkpoint_root": (
                checkpoint_root(checkpoint_volume) if streams and checkpoint_volume else None
            ),
        }
        path = jobs.notebook_path()
        jobs.upload(path)
        spec_path = jobs.upload_spec(path, spec)
        run_id = jobs.submit(path, spec_path)
        payload = jobs.wait(run_id)
    except Exception as exc:
        if run_id is not None:
            # Abandoned (timeout, sustained poll failure): stop the job so it stops billing.
            _best_effort(jobs.cancel, run_id)
        reason = safe_failure_reason(exc)
        log.warning("dqx_batch_failed", error_type=type(exc).__name__, checks=len(specs))
        return [
            refused.get(i) or _error_outcome(t, f"the dqx job failed: {reason}")
            for i, (t, _config) in enumerate(specs)
        ]
    finally:
        if spec_path is not None:
            _best_effort(jobs.delete, spec_path)
    mapped = outcomes_from_payload(specs, payload, table_key=table_key)
    return [refused.get(i) or mapped[i] for i in range(len(specs))]


def _best_effort(call: Callable[..., Any], *args: Any) -> None:
    try:
        call(*args)
    except Exception as exc:
        log.warning("dqx_cleanup_failed", step=call.__name__, error_type=type(exc).__name__)
