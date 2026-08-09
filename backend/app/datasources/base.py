"""Datasource adapter seam.

Every datasource (Snowflake now; ADLS / S3 / Unity Catalog later) executes DQ
checks behind one ``CheckRunner`` interface that speaks GX-agnostic DTOs. The
GX-specific machinery lives entirely inside each adapter, so the run-service and
its tests depend only on the types here — never on Great Expectations internals.
This is also the seam that lets v1.1 swap GX for DQX on Unity Catalog (CLAUDE.md
§5) without rippling into the suite / check / result layer.

`CheckSpec` goes in (a check pulled from the DB); `CheckOutcome` comes out, one
per check, shaped to map cleanly onto the `results` table columns. Adapters
translate GX results into these DTOs; tests provide a fake `CheckRunner` and
never touch a live datasource.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

# One bound for every failing-row sample that lands in `Result.sample_failures`
# (#1196), and — since #1229 — for a list-shaped `Result.observed_value` (a
# set-oriented expectation's full observed distinct-value set). It lives on the
# datasource seam because multiple producers write these columns — the GX path
# writes both (`gx_runner._extract_sample_failures` for `sample_failures`,
# `gx_runner._bounded_observed_value` for `observed_value`), while the comparison
# path (`comparison.SAMPLE_LIMIT`) bounds only `sample_failures` (a comparison
# result's `observed_value` is always a dict of scalar counts, never a list) —
# and the read path (`run_service`) re-applies it so already-persisted oversized
# rows stop shipping unbounded payloads too. Keeping a single constant means
# raising it can't leave result paths disagreeing. 20 matches GX's own
# `partial_unexpected_count` default and what the run-detail UI renders
# (`RunDetail.tsx` `MAX_SAMPLE_ROWS`).
SAMPLE_ROW_CAP = 20


@dataclass(frozen=True)
class CheckSpec:
    """One expectation to evaluate, sourced from a `checks` row.

    `expectation_type` is the GX snake_case name (e.g.
    ``expect_column_values_to_not_be_null``); `kwargs` are its parameters
    (e.g. ``{"column": "id"}``). Adapters own the translation to the concrete
    GX expectation class.
    """

    expectation_type: str
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one check, shaped for the `results` table.

    `observed_value` / `expected_value` / `sample_failures` land in the
    matching JSONB columns. `sample_failures` may contain real data rows, so it
    is governed by the retention sweep and must only ever be logged through the
    PII-redacting structlog chain.

    `errored` marks a check that could not be *evaluated* (the runner caught an
    exception while computing it — e.g. it references a missing column), as
    opposed to a check that evaluated and *failed* (`success=False`). The two are
    distinct result statuses (#122): an errored check maps to ``error`` (no
    severity, no metric), a failed one to a severity tier. A single errored check
    never fails its siblings — they still evaluate and persist.

    `skipped` is the third operational outcome (#593): the check ran fine, but its
    *precondition* wasn't met, so there is no honest verdict to give — an anomaly
    monitor whose baseline holds fewer points than its `min_points` cold start is
    the first case. It maps to the ``skip`` result status (already per-row valid;
    until now only `run_service.skip_run` produced it, run-wide). Deliberately
    distinct from `errored` (nothing went wrong) and from a fabricated
    `success=True` (a fake pass would count as a clean check in the health score
    and hide the fact that the monitor isn't watching anything yet).
    """

    expectation_type: str
    success: bool
    observed_value: dict[str, Any] | None = None
    expected_value: dict[str, Any] | None = None
    sample_failures: dict[str, Any] | None = None
    errored: bool = False
    error_message: str | None = None
    skipped: bool = False
    # The badness scalar a *monitor* (freshness/volume, ADR 0012) computed directly
    # — age-hours, % volume deviation. `severity.extract_metric` prefers this when
    # set, so monitor kinds band the same way (higher = worse, ADR 0016) without
    # abusing the GX unexpected-% sample shape. None for GX expectations, whose
    # metric is parsed from the sample (or, for custom-SQL, from `observed_value`
    # — see `severity.py`).
    metric_value: float | None = None


@dataclass(frozen=True)
class MonitorSpec:
    """One monitor to evaluate (freshness/volume, ADR 0012), sourced from a `checks`
    row whose ``kind`` is a monitor kind. ``config`` is the check's JSONB config
    (e.g. ``{"column": "loaded_at"}`` / ``{"min_rows": 1000, "max_rows": 5000}``).

    A monitor isn't a GX expectation — it runs a scalar SQL aggregate — so it has its
    own spec/runner path distinct from `CheckSpec`/`CheckRunner`.
    """

    kind: str
    config: dict[str, Any]


@dataclass(frozen=True)
class SuiteOutcome:
    """Aggregate result of running a list of checks against one table."""

    success: bool
    checks: list[CheckOutcome]


@runtime_checkable
class CheckRunner(Protocol):
    """Executes a set of checks against a single table and returns outcomes."""

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome: ...


@runtime_checkable
class MonitorRunner(Protocol):
    """A datasource runner that can also evaluate **monitor** kinds by running
    scalar aggregates against the table — Snowflake / Unity Catalog (SQL) and
    Iceberg (native scan) today.

    The run path gates on ``supported_monitor_kinds`` — the runner-advertised
    capability set (#429) — NOT on ``isinstance`` against this Protocol: a
    ``runtime_checkable`` isinstance is a name-only structural match, so an
    unrelated ``run_monitors`` method would pass the gate and then TypeError at
    the call instead of raising the clean unsupported-kind error. Keeping the
    capability data-driven also keeps the monitor-kind seam orthogonal to the
    datasource seam (ADR 0012): new kinds (#592/#593) extend a runner's set,
    never the orchestrator's dispatch.

    One ``CheckOutcome`` per ``MonitorSpec``, in order. A monitor that can't be
    evaluated (bad column, type mismatch) yields an ``errored`` outcome rather
    than failing its siblings — mirroring `CheckRunner` semantics.
    """

    supported_monitor_kinds: frozenset[str]

    def run_monitors(
        self,
        *,
        table: str,
        schema: str | None,
        monitors: list[MonitorSpec],
    ) -> list[CheckOutcome]: ...


@runtime_checkable
class ConnectionAdapter(Protocol):
    """Per-datasource-type connection behaviour: config validation + live test.

    The two things that vary across connection types (Snowflake now; ADF, ADLS,
    S3, Unity Catalog next) behind one interface, so connection-CRUD service code
    dispatches by ``connection.type`` and never branches on it. Each adapter owns
    its own pydantic config model; both methods take the raw config dict so the
    adapter is the single source of truth for that type's shape.

    `validate_config` parses + validates a stored/incoming config (raising
    pydantic ``ValidationError`` on bad input) and returns the normalised model.
    `test` resolves connectivity against the live datasource using the config +
    its secret, raising on failure. Adapters never touch the DB or SecretStore —
    the caller resolves the secret and hands it in.

    Some types need MORE than one credential (an Iceberg SQL catalog needs the
    storage key *and* the catalog DB password). Rather than smuggle the second one
    into non-secret `config` — the #754/#826 bug — the caller resolves every extra
    secret named in config and passes them as keyword arguments. Adapters that need
    none simply ignore them (`**_`), so the seam's "caller resolves secrets"
    invariant holds for one credential or five.

    `test`'s ``secret`` is ``str | None`` because a handful of types have NO
    credential at all in some configurations (Iceberg: a credential-less
    catalog; dbt: a local ``file://`` artifacts path) — `create_connection`
    already accepts ``secret=None`` for them (`optionalSecret` in the frontend's
    `connectionFormSpec.ts`). An adapter for which a secret is genuinely
    mandatory (Snowflake, ADLS, S3, Unity Catalog, ADF, Airflow) still declares
    the wider parameter type (a Protocol's implementations can't narrow it,
    mypy's contravariance check enforces that structurally — #351 review), but
    guards with an explicit ``if secret is None: raise`` at the top of its own
    `test`, narrowing for the rest of the method; it is never actually called
    with ``None`` because `connection_service.test_connection` /
    `test_draft_connection` gate on the ``secret_optional`` class attribute
    below before it can happen.

    ``secret_optional: bool`` (declared on the concrete adapter class, default
    ``False`` via ``getattr(adapter, "secret_optional", False)`` at the two
    call sites — deliberately NOT part of this Protocol's required surface, so
    the five mandatory-secret adapters don't have to restate the default) marks
    a type whose `test` tolerates ``secret=None``. Only `IcebergConnectionAdapter`
    and `DbtConnectionAdapter` set it ``True``.
    """

    def validate_config(self, raw: dict[str, Any]) -> BaseModel: ...

    def test(self, raw: dict[str, Any], secret: str | None, **extra_secrets: Any) -> None: ...


@runtime_checkable
class ExpiringCredentialAdapter(Protocol):
    """A `ConnectionAdapter` whose credential *states its own expiry* (#838).

    Optional, and deliberately narrow. An adapter implements this only when the
    expiry is **in the credential** — an Azure storage SAS carries `se=`. An
    adapter whose credential has no readable lifetime (an S3 access key, a
    Snowflake key-pair, a Databricks PAT) simply does not implement it and is
    silent: `None` and not-implemented both mean "unknown", never "never expires".
    Guessing a lifetime would be worse than saying nothing, because a confident
    wrong date is an outage with an alibi.

    Unlike `MonitorRunner` — where the run path gates on a capability *set* rather
    than `isinstance`, because a name-only structural match would TypeError at the
    call — an `isinstance` gate is fine here: the caller (`registry.credential_expiry`)
    is the only one, and it treats *any* failure as unknown, so a false structural
    match degrades to silence instead of an exception escaping.

    Implementations must not log, raise with, or otherwise echo the credential.
    """

    def credential_expiry(
        self, raw: dict[str, Any], secret: str, **extra_secrets: Any
    ) -> datetime | None: ...


@dataclass(frozen=True)
class BatchSpec:
    """An unresolved flat-file batch selector (resolved live by `materialize_path`).

    ``pattern`` is a regex whose first capture group is the batch key; ``strategy``
    is ``latest`` (greatest key) or ``specific`` (``batch`` key); ``prefix`` scopes
    the object listing.
    """

    prefix: str
    pattern: str
    strategy: str
    batch: str | None


@dataclass(frozen=True)
class ResolvedTarget:
    """The runner inputs a suite resolves to. ``table`` carries the file path for
    flat-file datasources; ``catalog`` is set only for Unity Catalog. ``batch`` is
    set only for a flat-file *batch* target, in which case ``table`` is empty until
    `materialize_path` lists the store and resolves the concrete path."""

    table: str
    schema: str | None
    catalog: str | None
    batch: BatchSpec | None = None


class TargetShapeError(ValueError):
    """A suite target is missing or malformed for its datasource type (#727).

    Raised by the per-type resolvers in `registry.py` and translated by
    `services.run_target` into the API-facing `SuiteTargetInvalidError`. The
    datasource layer states the shape problem; the service layer owns the HTTP
    contract, so neither has to know the other's job.
    """
