"""The user-facing ADF gate pipeline (ADR 0046). ADF honours secureInput/secureOutput only under an
activity's `policy`; anywhere else they are ignored and the PAT lands in the run history."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PIPELINE = Path(__file__).resolve().parents[3] / "integrations" / "adf" / "dataq_gate_pipeline.json"


def _activities(activities: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for activity in activities:
        yield activity
        nested = activity.get("typeProperties", {})
        for key in ("activities", "ifTrueActivities", "ifFalseActivities"):
            yield from _activities(nested.get(key, []))


def _by_name() -> dict[str, dict[str, Any]]:
    pipeline = json.loads(PIPELINE.read_text())
    return {a["name"]: a for a in _activities(pipeline["properties"]["activities"])}


def test_the_token_is_kept_out_of_the_run_history() -> None:
    activities = _by_name()
    assert activities["Get DataQ token"]["policy"]["secureOutput"] is True
    assert activities["Ask DataQ gate"]["policy"]["secureInput"] is True


def test_no_secure_flag_sits_where_adf_ignores_it() -> None:
    for activity in _by_name().values():
        assert not {"secureInput", "secureOutput"} & activity.keys(), activity["name"]
