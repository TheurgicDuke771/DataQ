"""render.py's evidence-summary + RCA-narrative clauses (#1647): pure formatting,
no DB — the honesty rule that absence renders explicitly rather than being
silently dropped.
"""

from __future__ import annotations

import uuid

from backend.app.alerting import render
from backend.app.alerting.base import IncidentCard

# ── _upstream_pipeline_clause ────────────────────────────────────────────────


def test_upstream_clause_none_reads_as_normal_not_unknown() -> None:
    """A manually-triggered or scheduled run has no upstream pipeline by
    design — the majority of runs. That must not read as "we don't know".
    """
    assert (
        render._upstream_pipeline_clause(None) == "not pipeline-triggered (manual or scheduled run)"
    )


def test_upstream_clause_malformed_reads_as_unavailable() -> None:
    assert render._upstream_pipeline_clause("not a dict") == "upstream pipeline: unavailable"


def test_upstream_clause_with_delay() -> None:
    clause = render._upstream_pipeline_clause(
        {"provider": "airflow", "status": "succeeded", "delay_seconds_vs_history": 540.0}
    )
    assert clause == "upstream airflow run succeeded (+540s vs history)"


def test_upstream_clause_negative_delay_keeps_the_sign() -> None:
    clause = render._upstream_pipeline_clause(
        {"provider": "adf", "status": "succeeded", "delay_seconds_vs_history": -30.0}
    )
    assert "-30s" in clause


def test_upstream_clause_without_a_delay_baseline() -> None:
    clause = render._upstream_pipeline_clause(
        {"provider": "dbt", "status": "succeeded", "delay_seconds_vs_history": None}
    )
    assert clause == "upstream dbt run succeeded"


# ── _sibling_failures_clause ─────────────────────────────────────────────────


def test_sibling_clause_none_is_unavailable_not_solo() -> None:
    """`sibling_checks` always resolves to a list unless the layer itself
    raised — `None` here is a genuine "could not be built".
    """
    assert render._sibling_failures_clause(None) == "same-run siblings: unavailable"


def test_sibling_clause_empty_list_is_genuinely_solo() -> None:
    assert render._sibling_failures_clause([]) == "no other checks in this run"


def test_sibling_clause_all_passing() -> None:
    siblings = [{"check_name": "a", "status": "pass"}, {"check_name": "b", "status": "pass"}]
    assert render._sibling_failures_clause(siblings) == "2 other check(s) in this run, all passing"


def test_sibling_clause_some_failing() -> None:
    siblings = [
        {"check_name": "a", "status": "pass"},
        {"check_name": "b", "status": "fail"},
        {"check_name": "c", "status": "warn"},
    ]
    assert (
        render._sibling_failures_clause(siblings) == "2/3 other check(s) in this run also failing"
    )


# ── _blast_radius_clause ─────────────────────────────────────────────────────


def test_blast_radius_clause_none_is_unavailable() -> None:
    assert render._blast_radius_clause(None) == "downstream impact: unavailable"


def test_blast_radius_clause_empty_never_claims_the_all_clear() -> None:
    """An empty list can mean "never resolved", "genuine leaf", or "no
    lineage recorded at all" — never claim nothing is affected (#828 class).
    """
    clause = render._blast_radius_clause([])
    assert clause == "no downstream lineage recorded"
    assert "affected" not in clause.lower()
    assert "clear" not in clause.lower()


def test_blast_radius_clause_populated() -> None:
    blast = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert render._blast_radius_clause(blast) == "3 downstream asset(s) potentially affected"


# ── evidence_summary_clause ──────────────────────────────────────────────────


def test_evidence_summary_clause_no_card_at_all() -> None:
    assert render.evidence_summary_clause(None) == "evidence: unavailable"


def test_evidence_summary_clause_composes_all_three_parts() -> None:
    evidence = {
        "upstream_pipeline_run": {"provider": "airflow", "status": "succeeded"},
        "sibling_checks": [],
        "downstream_blast_radius": [{"name": "mart.revenue"}],
    }
    clause = render.evidence_summary_clause(evidence)
    assert clause == (
        "upstream airflow run succeeded; no other checks in this run; "
        "1 downstream asset(s) potentially affected"
    )


# ── narrative_clause ──────────────────────────────────────────────────────────


def test_narrative_clause_none_when_never_generated() -> None:
    assert render.narrative_clause(None) == ""


def test_narrative_clause_blank_summary_renders_nothing() -> None:
    assert render.narrative_clause({"summary": "   ", "ranked_hypotheses": []}) == ""


def test_narrative_clause_summary_only_when_no_hypotheses() -> None:
    narrative = {"summary": "Looks like a volume drop.", "ranked_hypotheses": []}
    assert render.narrative_clause(narrative) == "AI summary: Looks like a volume drop."


def test_narrative_clause_includes_top_cause_and_evidence_refs() -> None:
    narrative = {
        "summary": "Looks like a volume drop.",
        "ranked_hypotheses": [
            {
                "cause": "Upstream pipeline delivered fewer rows.",
                "confidence": "high",
                "evidence_refs": ["metric_trend", "upstream_pipeline_run"],
            }
        ],
    }
    clause = render.narrative_clause(narrative)
    assert clause == (
        "AI summary: Looks like a volume drop. — top cause (high): "
        "Upstream pipeline delivered fewer rows. (via metric_trend, upstream_pipeline_run)"
    )


def test_narrative_clause_a_causeless_top_hypothesis_falls_back_to_summary_only() -> None:
    narrative = {
        "summary": "Looks like a volume drop.",
        "ranked_hypotheses": [{"confidence": "low", "evidence_refs": ["metric_trend"]}],
    }
    assert render.narrative_clause(narrative) == "AI summary: Looks like a volume drop."


# ── incident_line composition (uses the real IncidentCard dataclass) ────────


def _card(
    evidence: dict[str, object] | None, narrative: dict[str, object] | None = None
) -> IncidentCard:
    return IncidentCard(
        incident_id=uuid.UUID("12345678-0000-0000-0000-000000000000"),
        check_id=uuid.uuid4(),
        check_name="not-null id",
        status="fail",
        occurrence_count=1,
        is_new=True,
        evidence=evidence,
        narrative=narrative,
    )


def test_incident_line_appends_evidence_summary_and_narrative() -> None:
    card = _card(
        evidence={
            "upstream_pipeline_run": None,
            "sibling_checks": [],
            "downstream_blast_radius": [],
        },
        narrative={
            "summary": "A volume drop.",
            "ranked_hypotheses": [
                {
                    "cause": "pipeline delay",
                    "confidence": "medium",
                    "evidence_refs": ["metric_trend"],
                }
            ],
        },
    )
    line = render.incident_line(card)
    assert line.startswith("Incident 12345678 (not-null id) — fail, new · ")
    assert "not pipeline-triggered" in line
    assert "AI summary: A volume drop." in line


def test_incident_line_omits_narrative_clause_when_none_generated() -> None:
    card = _card(evidence={"sibling_checks": []}, narrative=None)
    line = render.incident_line(card)
    assert "AI summary" not in line
