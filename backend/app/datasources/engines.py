"""Connection-anchored check-engine capability map (ADR 0036)."""

from __future__ import annotations

from backend.app.db.models import GX_ENGINE

# Datasource connection types only — an orchestration provider offers no engines (it has no checks).
_OFFERED: dict[str, frozenset[str]] = {
    # 'dmf' rides SnowflakeCheckRunner.run_native_check (ADR 0036 §6, #895);
    # `test_offered_native_engines_are_runnable` pins offer ⇔ runner support.
    "snowflake": frozenset({GX_ENGINE, "dmf"}),
    "adls_gen2": frozenset({GX_ENGINE}),
    "s3": frozenset({GX_ENGINE}),
    "unity_catalog": frozenset({GX_ENGINE}),  # "dqx" trigger-gated (ADR 0036 §6)
    "iceberg": frozenset({GX_ENGINE}),
}


def engines_for(connection_type: str) -> frozenset[str]:
    """Engines ``connection_type`` offers; empty for a non-datasource type."""
    return _OFFERED.get(connection_type, frozenset())
