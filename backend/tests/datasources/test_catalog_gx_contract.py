"""Catalog↔GX contract test (#205).

The check editor's ``expectationCatalog.ts`` is the frontend's source of truth
for expectation ``type`` (snake_case → GX class) and each config field name
(→ GX kwarg). The backend deliberately has NO server catalog — config is
free-form kwargs title-cased to a GX class in ``gx_runner`` — so this coupling
has zero compile-time check: a catalog typo or a GX point-release kwarg rename
ships fine and only fails at suite-run time on the worker.

This test pins the seam against the PINNED GX version, resolving each catalog
entry through the very same ``_expectation_class_name``/``getattr`` path the
runner uses. The input is ``tests/fixtures/expectation_catalog.json``, kept in
lock-step with the live TS catalog by the frontend drift-guard
(``frontend/tests/components/catalogContract.test.ts`` — regenerate with
``UPDATE_CATALOG_FIXTURE=1``). A GX bump or a catalog edit that breaks the
pairing now fails HERE, in CI, not on the worker.
"""

import json
from pathlib import Path
from typing import Any

import great_expectations.expectations as gxe
import pytest

from backend.app.datasources import monitors
from backend.app.datasources.gx_runner import _expectation_class_name
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE, QUERY_KEY

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "expectation_catalog.json"

with _FIXTURE.open() as f:
    CATALOG: list[dict[str, Any]] = json.load(f)

EXPECTATIONS = [e for e in CATALOG if e["kind"] == "expectation"]
MONITORS = [e for e in CATALOG if e["kind"] != "expectation"]

_ids = [e["type"] for e in EXPECTATIONS]


def test_fixture_is_present_and_nonempty() -> None:
    """A gutted fixture must fail loudly, not vacuously pass the loops below."""
    assert len(EXPECTATIONS) >= 8
    assert len(MONITORS) == 2


@pytest.mark.parametrize("entry", EXPECTATIONS, ids=_ids)
def test_catalog_type_resolves_to_a_gx_class(entry: dict[str, Any]) -> None:
    class_name = _expectation_class_name(entry["type"])
    assert hasattr(gxe, class_name), (
        f"Catalog type {entry['type']!r} does not resolve to a GX expectation "
        f"(no gxe.{class_name}) — a catalog typo, or the pinned GX renamed it"
    )


@pytest.mark.parametrize("entry", EXPECTATIONS, ids=_ids)
def test_catalog_fields_are_accepted_gx_kwargs(entry: dict[str, Any]) -> None:
    cls = getattr(gxe, _expectation_class_name(entry["type"]))
    # GX 1.17 models ride pydantic's v1-compat layer: the accepted-kwargs dict
    # is `__fields__` (there is no class-level `model_fields` under pydantic 2.13).
    accepted = set(cls.__fields__)
    unknown = [name for name in entry["fields"] if name not in accepted]
    assert not unknown, (
        f"{entry['type']}: field(s) {unknown} are not kwargs of {cls.__name__} — "
        f"the check would save fine and blow up in the Celery runner"
    )


@pytest.mark.parametrize("entry", EXPECTATIONS, ids=_ids)
def test_catalog_entry_constructs_with_representative_kwargs(entry: dict[str, Any]) -> None:
    """Beyond field-name membership: the class actually instantiates with the
    catalog's fields populated (pydantic validators accept the shape)."""
    samples: dict[str, Any] = {
        "column": "ORDER_ID",
        "min_value": 1,
        "max_value": 10,
        "value_set": ["a", "b"],
        "regex": r"^\d+$",
        QUERY_KEY: "SELECT * FROM {batch_id} WHERE amount < 0",
    }
    cls = getattr(gxe, _expectation_class_name(entry["type"]))
    kwargs = {name: samples[name] for name in entry["fields"]}
    cls(**kwargs)  # raises if the pinned GX rejects the catalog's shape


def test_custom_sql_entry_matches_backend_constants() -> None:
    """ADR 0019: the custom-SQL type + query key are shared constants on both
    sides; the catalog must carry exactly those."""
    entry = next(e for e in CATALOG if e["type"] == CUSTOM_SQL_EXPECTATION_TYPE)
    assert entry["fields"] == [QUERY_KEY]


def test_monitor_entries_match_backend_kinds() -> None:
    """Monitor kinds bypass GX (ADR 0012); their catalog types must match the
    backend's canonical `monitor:<kind>` mapping and known kinds."""
    assert {e["kind"] for e in MONITORS} == set(monitors.MONITOR_KINDS)
    for entry in MONITORS:
        assert entry["type"] == monitors.monitor_expectation_type(entry["kind"])


def test_monitor_fields_match_engine_config_keys() -> None:
    """The monitor engine reads exactly these config keys (monitors.py):
    freshness → column; volume → min_rows/max_rows."""
    by_kind = {e["kind"]: e["fields"] for e in MONITORS}
    assert by_kind[monitors.FRESHNESS] == ["column"]
    assert sorted(by_kind[monitors.VOLUME]) == ["max_rows", "min_rows"]
