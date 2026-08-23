"""#457 — trip-wire for partial-index / predicate drift across OrchestrationProviders."""

from __future__ import annotations

import re

from backend.app.db.models import ORCHESTRATION_PROVIDERS, Run
from backend.app.services.orchestration_service import _ORCH_TRIGGER_PREDICATE

_PREFIX_RE = re.compile(r"triggered_by LIKE '([a-z_]+):%'")


def _covered_prefixes(predicate_sql: str) -> set[str]:
    return set(_PREFIX_RE.findall(predicate_sql))


def _model_predicate() -> str:
    for arg in Run.__table_args__:
        if getattr(arg, "name", None) == "uq_runs_suite_triggered_by":
            return str(arg.dialect_options["postgresql"]["where"])
    raise AssertionError("uq_runs_suite_triggered_by is missing from Run.__table_args__")


def test_model_partial_index_covers_every_orchestration_provider() -> None:
    """The dedup index's predicate must name every provider — no more, no fewer."""
    assert _covered_prefixes(_model_predicate()) == set(ORCHESTRATION_PROVIDERS)


def test_service_upsert_predicate_covers_every_orchestration_provider() -> None:
    """The `ON CONFLICT` predicate must match the index it targets."""
    assert _covered_prefixes(str(_ORCH_TRIGGER_PREDICATE)) == set(ORCHESTRATION_PROVIDERS)


def test_model_and_service_predicates_agree() -> None:
    """Stated separately from the two above so a failure says WHICH drifted."""
    assert _covered_prefixes(_model_predicate()) == _covered_prefixes(str(_ORCH_TRIGGER_PREDICATE))
