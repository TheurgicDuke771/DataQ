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

import functools
import json
from pathlib import Path
from typing import Any

import great_expectations.expectations as gxe
import pytest

from backend.app.datasources import monitors
from backend.app.datasources.gx_runner import _expectation_class_name
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE, QUERY_KEY

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "expectation_catalog.json"


@functools.cache
def _catalog() -> list[dict[str, Any]]:
    """Parse lazily (and once): a missing/corrupt fixture must fail THESE tests,
    not abort the whole session as a collection error."""
    with _FIXTURE.open() as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


def _expectations() -> list[dict[str, Any]]:
    return [e for e in _catalog() if e["kind"] == "expectation"]


def _monitors() -> list[dict[str, Any]]:
    return [e for e in _catalog() if e["kind"] in monitors.MONITOR_KINDS]


def _comparisons() -> list[dict[str, Any]]:
    return [e for e in _catalog() if e["kind"] == "comparison"]


# Parametrization ids come from a SEPARATE minimal read that tolerates a broken
# fixture (falling back to indices), so collection itself never hard-fails.
def _expectation_params() -> list[Any]:
    try:
        entries = _expectations()
    except Exception:
        # Broken fixture: emit one placeholder param so the body's _catalog()
        # call reports the parse error as an ordinary test failure.
        return [pytest.param(None, id="fixture-unreadable")]
    return [pytest.param(e, id=e["type"]) for e in entries]


def test_fixture_is_present_and_nonempty() -> None:
    """A gutted fixture must fail loudly, not vacuously pass the loops below."""
    assert len(_expectations()) >= 8
    assert len(_monitors()) == 3  # freshness, volume, schema_drift (#592)
    assert len(_comparisons()) == 2  # records + columns grains (#799)


def test_comparison_entries_match_backend_canonical_types() -> None:
    """The comparison catalog entries (ADR 0015 + #799) must carry exactly the
    backend's canonical expectation_types; they bypass GX (no fields — the
    dedicated side-by-side form authors them)."""
    from backend.app.services.check_service import COMPARISON_EXPECTATION_TYPES

    entries = _comparisons()
    assert sorted(e["type"] for e in entries) == sorted(COMPARISON_EXPECTATION_TYPES)
    assert all(e["fields"] == [] for e in entries)


@pytest.mark.parametrize("entry", _expectation_params())
def test_catalog_type_resolves_to_a_gx_class(entry: dict[str, Any] | None) -> None:
    entry = entry if entry is not None else _expectations()[0]
    class_name = _expectation_class_name(entry["type"])
    assert hasattr(gxe, class_name), (
        f"Catalog type {entry['type']!r} does not resolve to a GX expectation "
        f"(no gxe.{class_name}) — a catalog typo, or the pinned GX renamed it"
    )


@pytest.mark.parametrize("entry", _expectation_params())
def test_catalog_fields_are_accepted_gx_kwargs(entry: dict[str, Any] | None) -> None:
    entry = entry if entry is not None else _expectations()[0]
    cls = getattr(gxe, _expectation_class_name(entry["type"]))
    # GX 1.17 models ride pydantic's v1-compat layer: the accepted-kwargs dict
    # is `__fields__` (there is no class-level `model_fields` under pydantic 2.13).
    accepted = set(cls.__fields__)
    unknown = [name for name in entry["fields"] if name not in accepted]
    assert not unknown, (
        f"{entry['type']}: field(s) {unknown} are not kwargs of {cls.__name__} — "
        f"the check would save fine and blow up in the Celery runner"
    )


@pytest.mark.parametrize("entry", _expectation_params())
def test_catalog_entry_constructs_with_representative_kwargs(entry: dict[str, Any] | None) -> None:
    """Beyond field-name membership: the class actually instantiates with the
    catalog's fields populated (pydantic validators accept the shape)."""
    entry = entry if entry is not None else _expectations()[0]
    samples: dict[str, Any] = {
        "column": "ORDER_ID",
        "min_value": 1,
        "max_value": 10,
        "value_set": ["a", "b"],
        "regex": r"^\d+$",
        "type_": "int64",
        QUERY_KEY: "SELECT * FROM {batch} WHERE amount < 0",
    }
    missing = [name for name in entry["fields"] if name not in samples]
    assert not missing, (
        f"{entry['type']}: new catalog field(s) {missing} have no representative "
        f"sample here — add one to `samples` so construction stays exercised"
    )
    cls = getattr(gxe, _expectation_class_name(entry["type"]))
    kwargs = {name: samples[name] for name in entry["fields"]}
    cls(**kwargs)  # raises if the pinned GX rejects the catalog's shape


def test_custom_sql_entry_matches_backend_constants() -> None:
    """ADR 0019: the custom-SQL type + query key are shared constants on both
    sides; the catalog must carry exactly those."""
    entry = next(e for e in _catalog() if e["type"] == CUSTOM_SQL_EXPECTATION_TYPE)
    assert entry["fields"] == [QUERY_KEY]


def test_monitor_entries_match_backend_kinds() -> None:
    """Monitor kinds bypass GX (ADR 0012); their catalog types must match the
    backend's canonical `monitor:<kind>` mapping and known kinds."""
    assert {e["kind"] for e in _monitors()} == set(monitors.MONITOR_KINDS)
    for entry in _monitors():
        assert entry["type"] == monitors.monitor_expectation_type(entry["kind"])


def test_monitor_fields_match_engine_config_keys() -> None:
    """The monitor engine reads exactly these config keys (monitors.py):
    freshness → column; volume → min_rows/max_rows."""
    by_kind = {e["kind"]: e["fields"] for e in _monitors()}
    assert by_kind[monitors.FRESHNESS] == ["column"]
    assert sorted(by_kind[monitors.VOLUME]) == ["max_rows", "min_rows"]


# ─────────── catalog ↔ dimension-derivation contract (ADR 0038, #124) ──────────


def _dimension_params() -> list[Any]:
    try:
        return [pytest.param(e, id=e["type"]) for e in _catalog()]
    except Exception:
        return []


@pytest.mark.parametrize("entry", _dimension_params())
def test_catalog_dimension_matches_the_backend_derivation(entry: dict[str, Any]) -> None:
    """The editor pre-fills a check's dimension from the TS catalog, but the
    BACKEND derivation is what actually gets stored (ADR 0038).

    If the two disagree, the author is shown one classification and a different
    one is persisted — a silent wrong answer with no error anywhere, and one that
    would quietly skew the #889 coverage view. There is no compile-time link
    between a TS object literal and a Python dict, so this is the only thing
    holding them together.
    """
    from backend.app.services.check_dimension import derive_dimension

    derived = derive_dimension(expectation_type=entry["type"], kind=entry["kind"])
    assert derived == entry["dimension"], (
        f"{entry['type']}: catalog says {entry['dimension']!r}, "
        f"backend derives {derived!r} — the maps have drifted"
    )


def test_every_catalog_dimension_is_canonical() -> None:
    """A catalog value outside the vocabulary would pass the editor's select and
    then 422 (or violate the table CHECK) at save time."""
    from backend.app.db.models import DQ_DIMENSIONS

    for entry in _catalog():
        assert entry["dimension"] is None or entry["dimension"] in DQ_DIMENSIONS


def test_custom_sql_is_deliberately_unclassified_in_both_maps() -> None:
    """Pinned as a decision, not left as an accident (ADR 0038 §3): an arbitrary
    SQL predicate has no derivable dimension, and guessing one would put confident
    nonsense in the scorecard."""
    from backend.app.services.check_dimension import derive_dimension

    entry = next(e for e in _catalog() if e["type"] == CUSTOM_SQL_EXPECTATION_TYPE)
    assert entry["dimension"] is None
    assert derive_dimension(expectation_type=entry["type"], kind=entry["kind"]) is None
