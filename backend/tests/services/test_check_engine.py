"""`check_service.validate_engine` + the `datasources.engines` capability map
(ADR 0036 slice 1, #895).

Pure — no DB. The wiring through CRUD / import / restore is exercised at the API
layer in `tests/api/test_checks.py`; this file covers the validator and the two
invariants the seam rests on: **gx is universal** (every datasource type offers
it) and **offered ⇒ runnable** (the capability map never names a connection type
the runner registry doesn't know).
"""

from __future__ import annotations

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
    # Offered ⇒ runnable: the map's key set is exactly the set of types with a
    # registered CheckRunner builder. A datasource added to one and not the
    # other either can't author checks (missing here) or authors checks that
    # cannot run (missing there).
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
    # 'dmf' is vocabulary-valid but unoffered until DmfCheckRunner registers
    # (#895 slice 2) — the 422 must say what IS offered, not just refuse.
    with pytest.raises(CheckConfigInvalidError) as exc:
        validate_engine("dmf", connection_type="snowflake")
    assert exc.value.detail["offered"] == [GX_ENGINE]
    assert exc.value.detail["connection_type"] == "snowflake"


def test_native_engine_on_the_wrong_type_rejected() -> None:
    with pytest.raises(CheckConfigInvalidError):
        validate_engine("dmf", connection_type="s3")


def test_orchestration_connection_offers_nothing_even_gx() -> None:
    # A suite can never sit on an orchestration connection, but the validator
    # must not invent an offer if one ever reaches it.
    with pytest.raises(CheckConfigInvalidError) as exc:
        validate_engine(GX_ENGINE, connection_type="airflow")
    assert exc.value.detail["offered"] == []
