"""Connection-anchored check-engine capability map (ADR 0036).

The one place that answers "which engines does this connection TYPE offer".
`check_service.validate_engine` consults it at save time; the run path partitions
a run's checks by `check.engine` and treats an engine this map no longer offers
as a classified per-check ``error`` (never a silent skip).

Two deliberate properties:

* **Offered ⇒ runnable.** An engine appears here only once its runner exists, so
  save-time acceptance can never outrun the run path. The ADR's target matrix
  (`dmf` ⇔ snowflake, `dqx` ⇔ unity_catalog, `dataplex` ⇔ a future bigquery
  type) is wider than this map until each native build lands — snowflake gains
  ``dmf`` in the same change that registers `DmfCheckRunner` (#895).
* **Type gates the offer; the instance validates the reality** (ADR 0036 §3).
  This map is the type gate only. Whether a *particular* connection can run a
  native engine (edition, grants) is the phase-2 probe's job, stored on
  `Connection.engine_capabilities`.

Pure data on purpose: the API save path imports this without pulling in the
driver-heavy runner modules `datasources.registry` loads.
"""

from __future__ import annotations

from backend.app.db.models import GX_ENGINE

# Datasource connection types only — an orchestration provider offers no engines
# (it has no checks). Keys mirror `registry._RUNNER_BUILDERS`;
# `test_engine_map_matches_registry` pins the two together.
_OFFERED: dict[str, frozenset[str]] = {
    "snowflake": frozenset({GX_ENGINE}),  # + "dmf" when DmfCheckRunner registers (#895)
    "adls_gen2": frozenset({GX_ENGINE}),
    "s3": frozenset({GX_ENGINE}),
    "unity_catalog": frozenset({GX_ENGINE}),  # "dqx" trigger-gated (ADR 0036 §6)
    "iceberg": frozenset({GX_ENGINE}),
}


def engines_for(connection_type: str) -> frozenset[str]:
    """Engines ``connection_type`` offers; empty for a non-datasource type.

    Empty-not-raise: the caller turns "offers nothing" and "doesn't offer X"
    into the same 422, and an unknown type was already rejected upstream by
    connection CRUD.
    """
    return _OFFERED.get(connection_type, frozenset())
