"""#457 — trip-wire for partial-index / predicate drift across OrchestrationProviders.

The trigger-dedup unique index on `runs` is PARTIAL: it only covers rows whose
`triggered_by` carries an orchestration prefix. That predicate is spelled in three
places — the model's `__table_args__`, the migration that created it, and the
service constant used for the `ON CONFLICT` upsert — and all three must list every
provider in `ORCHESTRATION_PROVIDERS`.

**What this module checks.** The two Python spellings — the model's
`__table_args__` and the service constant — against `ORCHESTRATION_PROVIDERS` and
against each other.

The third spelling, the migration, is covered by `test_migration_parity.py`
(#990): it replays `alembic upgrade head` onto a scratch database and diffs the
result against the model, so a provider widened in the model but not in a
migration now fails there. Until that existed, this module could only say so in
prose — widen the vocabulary, the model and the service correctly, ship no
migration, and every test stayed green while production's predicate was stale and
dedup silently lapsed for the new provider.

Adding a provider without widening them is silent: dedup simply stops applying to
the new provider's runs, so a re-delivered webhook creates a duplicate run instead
of being rejected. Nothing fails, nothing logs — you find out from duplicate rows.

dbt (ADR 0029) was added and the widening WAS done by hand (migration
`c1d2e3f4a5b6`), which is exactly why this guard is worth having: it worked that
time, and there was no test that would have noticed if it hadn't.
"""

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
    """The `ON CONFLICT` predicate must match the index it targets.

    Postgres only uses a partial unique index for conflict resolution when the
    statement's `index_where` matches it, so a service predicate that drifts from
    the model's turns the upsert into a plain insert — duplicates, no error.
    """
    assert _covered_prefixes(str(_ORCH_TRIGGER_PREDICATE)) == set(ORCHESTRATION_PROVIDERS)


def test_model_and_service_predicates_agree() -> None:
    """Stated separately from the two above so a failure says WHICH drifted."""
    assert _covered_prefixes(_model_predicate()) == _covered_prefixes(str(_ORCH_TRIGGER_PREDICATE))
