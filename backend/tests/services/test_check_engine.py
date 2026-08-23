"""`check_service.validate_engine` + the `datasources.engines` capability map
(ADR 0036 slice 1, #895).
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.datasources import registry
from backend.app.datasources.engines import _OFFERED, engines_for
from backend.app.db.models import CHECK_ENGINES, GX_ENGINE, ORCHESTRATION_PROVIDERS
from backend.app.services.check_service import CheckConfigInvalidError, validate_engine

DATASOURCE_TYPES = sorted(_OFFERED)


# ── the capability map's invariants ──


def test_gx_is_universal_across_datasource_types() -> None:
    for conn_type in DATASOURCE_TYPES:
        assert GX_ENGINE in engines_for(conn_type)


def test_engine_map_matches_registry() -> None:
    # Offered ⇒ runnable: the map's key set is exactly the set of types with a registered
    # CheckRunner builder.
    assert set(_OFFERED) == set(registry._RUNNER_BUILDERS)


def test_every_offered_engine_is_in_the_vocabulary() -> None:
    # The DB CHECK constraint admits CHECK_ENGINES; an offered engine outside it
    # would 500 on the INSERT after passing validation.
    for offered in _OFFERED.values():
        assert offered <= set(CHECK_ENGINES)


def test_orchestration_providers_offer_no_engines() -> None:
    for provider in ORCHESTRATION_PROVIDERS:
        assert engines_for(provider) == frozenset()


# ── validate_engine ──


@pytest.mark.parametrize("conn_type", DATASOURCE_TYPES)
def test_gx_passes_on_every_datasource_type(conn_type: str) -> None:
    validate_engine(GX_ENGINE, connection_type=conn_type)  # must not raise


def test_unknown_engine_rejected_with_vocabulary() -> None:
    with pytest.raises(CheckConfigInvalidError) as exc:
        validate_engine("sparkles", connection_type="snowflake")
    assert "not recognised" in str(exc.value)
    assert exc.value.detail["known"] == sorted(CHECK_ENGINES)


def test_real_engine_not_offered_by_type_rejected_naming_the_offer() -> None:
    # 'dqx' is vocabulary-valid but trigger-gated (ADR 0036 §6) — the 422 must
    # say what IS offered, not just refuse.
    with pytest.raises(CheckConfigInvalidError) as exc:
        validate_engine("dqx", connection_type="unity_catalog")
    assert exc.value.detail["offered"] == [GX_ENGINE]
    assert exc.value.detail["connection_type"] == "unity_catalog"


def test_snowflake_offers_dmf() -> None:
    assert engines_for("snowflake") == frozenset({GX_ENGINE, "dmf"})


def test_offered_native_engines_are_runnable() -> None:
    # Offer ⇔ runner support (ADR 0036's offered ⇒ runnable, at the engine grain): every native
    # engine a type offers must be advertised by that type's runner class.
    from backend.app.datasources.snowflake import SnowflakeCheckRunner

    native_by_type = {"snowflake": SnowflakeCheckRunner.supported_native_engines}
    for conn_type in DATASOURCE_TYPES:
        offered_native = engines_for(conn_type) - {GX_ENGINE}
        assert offered_native == native_by_type.get(conn_type, frozenset())


def test_native_engine_on_the_wrong_type_rejected() -> None:
    with pytest.raises(CheckConfigInvalidError):
        validate_engine("dmf", connection_type="s3")


def test_orchestration_connection_offers_nothing_even_gx() -> None:
    # A suite can never sit on an orchestration connection, but the validator
    # must not invent an offer if one ever reaches it.
    with pytest.raises(CheckConfigInvalidError) as exc:
        validate_engine(GX_ENGINE, connection_type="airflow")
    assert exc.value.detail["offered"] == []


# ── validate_engine_compatibility (the DMF supported matrix, slice 2) ──


def _compat(engine: str = "dmf", **overrides: object) -> None:
    from decimal import Decimal

    from backend.app.services.check_service import validate_engine_compatibility

    kwargs: dict[str, Any] = {
        "kind": "expectation",
        "expectation_type": "dmf:null_count",
        "config": {"column": "order_id"},
        "warn_threshold": None,
        "fail_threshold": Decimal("10"),
        "critical_threshold": None,
    }
    kwargs.update(overrides)
    validate_engine_compatibility(engine, **kwargs)


def test_dmf_column_metric_with_column_and_threshold_passes() -> None:
    _compat()  # must not raise


def test_gx_engine_is_a_noop_here() -> None:
    # GX's own validators (GX gate, monitor validators) own its matrix — even a dmf: type under gx
    # is not THIS validator's business (the GX unknown-type gate rejects it downstream).
    _compat(engine="gx", expectation_type="dmf:null_count", fail_threshold=None)


@pytest.mark.parametrize("kind", ["comparison", "schema_drift", "anomaly", "volume"])
def test_dmf_rejects_kinds_outside_its_matrix(kind: str) -> None:
    with pytest.raises(CheckConfigInvalidError) as exc:
        _compat(kind=kind)
    assert exc.value.detail["supported_kinds"] == ["expectation", "freshness"]


def test_dmf_freshness_defers_to_the_monitor_validators() -> None:
    # freshness config is validate_monitor_check's job; this validator only checks the kind is in
    # the matrix.
    _compat(kind="freshness", expectation_type="monitor:freshness", config={})


def test_dmf_rejects_a_gx_expectation_type() -> None:
    with pytest.raises(CheckConfigInvalidError) as exc:
        _compat(expectation_type="expect_column_values_to_not_be_null")
    assert "dmf:null_count" in exc.value.detail["supported_types"]


def test_dmf_rejects_unknown_config_keys() -> None:
    with pytest.raises(CheckConfigInvalidError) as exc:
        _compat(config={"column": "order_id", "mostly": 0.9})
    assert exc.value.detail["unknown_keys"] == ["mostly"]


def test_dmf_rejects_a_bad_column_identifier() -> None:
    with pytest.raises(CheckConfigInvalidError):
        _compat(config={"column": "order id; DROP TABLE x"})


def test_dmf_bandable_metric_requires_a_positive_threshold() -> None:
    # The silent-green rule: without a fail/critical threshold the metric is
    # computed but never banded, so the check can never fail.
    with pytest.raises(CheckConfigInvalidError):
        _compat(fail_threshold=None)


def test_dmf_unique_count_refuses_thresholds() -> None:
    # derive_status bands higher-as-worse; a unique count degrades DOWNWARD, so
    # a threshold would invert its meaning — informational metric only.
    from decimal import Decimal

    with pytest.raises(CheckConfigInvalidError):
        _compat(expectation_type="dmf:unique_count", fail_threshold=Decimal("5"))
    _compat(expectation_type="dmf:unique_count", fail_threshold=None)  # thresholdless OK
