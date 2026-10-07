"""Column-lineage placement + dedup advice for check suggestions (#1710, ADR 0034 amendment).

Annotates each suggested check with where its column comes from, and whether an equivalent
check already runs upstream. It is ADVICE, never a filter: a column pair records derivation,
not equality (`amount → daily_revenue` is an aggregate; a join can fan out a key), so the
suggestion is always kept and the reviewer decides. Two rules keep the advice honest:

* **Only a same-name chain** — the same (engine-folded) column name on every hop — may claim
  "an equivalent check already covers this upstream" or "place it at the origin". A renamed or
  derived column gets its provenance shown and nothing more. Same name is a heuristic, not proof
  of a copy (`SUM(amount) AS amount` keeps the name), so the UI says "same column name upstream".
* **Equivalent = same type AND same parameters.** Monitor kinds (freshness, volume…) never get a
  recommendation: they measure the table they run on, not the column's upstream.
* **Suite detail stays behind grants** (ADR 0037): an upstream check the requester cannot view
  is counted, never named.
"""

from __future__ import annotations

import json
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Check, Suite
from backend.app.lineage import columns as lineage_columns
from backend.app.services.suite_authz import effective_permissions

log = get_logger(__name__)

#: The two recommendations a suggestion can carry (absent ⇒ nothing to say).
ALREADY_COVERED_UPSTREAM = "already_covered_upstream"
PLACE_AT_ORIGIN = "place_at_origin"

#: Monitor kinds measure the TABLE they run on (a stalled downstream job, a dropped row count) —
#: an upstream monitor on the same column says nothing about them, so no placement/dedup claim.
_MONITOR_PREFIX = "monitor:"


def _params(config: dict[str, Any] | None) -> str:
    """A check's parameters minus its column — what must match for two checks to be equivalent."""
    rest = {k: v for k, v in (config or {}).items() if k != "column"}
    return json.dumps(rest, sort_keys=True, default=str)


@dataclass(frozen=True)
class _Node:
    asset_id: uuid.UUID
    column: str  # the engine-folded spelling (a matching key, never displayed)


def _pass_through_nodes(
    trace: lineage_columns.ColumnTrace, namespaces: dict[uuid.UUID, str]
) -> dict[_Node, str]:
    """Upstream nodes reachable from the traced column through same-name hops only.

    Returns ``{node: display_column}``; the traced column itself is excluded.
    """

    def fold(asset_id: uuid.UUID, column: str) -> str:
        return lineage_columns.fold_column(namespaces.get(asset_id, ""), column)

    parents: dict[_Node, list[tuple[_Node, str]]] = {}
    for hop in trace.hops:
        down = _Node(hop.downstream_asset_id, fold(hop.downstream_asset_id, hop.downstream_column))
        up = _Node(hop.upstream_asset_id, fold(hop.upstream_asset_id, hop.upstream_column))
        # Same name ACROSS engines is compared on the folded spellings (both sides ours).
        if up.column.lower() == down.column.lower():
            parents.setdefault(down, []).append((up, hop.upstream_column))
    start = _Node(trace.asset_id, fold(trace.asset_id, trace.column))
    seen: dict[_Node, str] = {}
    queue: deque[_Node] = deque([start])
    while queue:
        node = queue.popleft()
        for parent, display in parents.get(node, []):
            if parent == start or parent in seen:
                continue
            seen[parent] = display
            queue.append(parent)
    return seen


def _equivalent_checks(
    session: Session,
    nodes: dict[_Node, str],
    namespaces: dict[uuid.UUID, str],
    *,
    expectation_type: str,
) -> list[tuple[Check, Suite, _Node]]:
    """Checks of the SAME expectation type on a pass-through upstream column (any parameters)."""
    if not nodes:
        return []
    by_asset: dict[uuid.UUID, set[str]] = {}
    for node in nodes:
        by_asset.setdefault(node.asset_id, set()).add(node.column)
    rows = session.execute(
        select(Check, Suite)
        .join(Suite, Suite.id == Check.suite_id)
        # A switched-off check covers nothing, here as on the scorecard.
        .where(
            Suite.asset_id.in_(list(by_asset)),
            Check.expectation_type == expectation_type,
            Check.enabled.is_(True),
        )
        .order_by(Check.name, Check.id)
    ).all()
    out: list[tuple[Check, Suite, _Node]] = []
    for check, suite in rows:
        column = (check.config or {}).get("column")
        if not isinstance(column, str) or suite.asset_id is None:
            continue
        folded = lineage_columns.fold_column(namespaces.get(suite.asset_id, ""), column)
        if folded in by_asset.get(suite.asset_id, set()):
            out.append((check, suite, _Node(suite.asset_id, folded)))
    return out


def placement_for(
    session: Session,
    *,
    asset_id: uuid.UUID,
    column: str,
    expectation_type: str,
    user_id: uuid.UUID | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The `lineage` annotation for one suggested check on ``asset_id.column``.

    An upstream check is an *equivalent* only with the same type AND the same parameters (minus
    the column): `between 0..1e6` upstream does not cover a suggested `between 0..100`. Same type
    with other parameters is counted as ``different_parameters_upstream``, never as coverage.
    """
    trace = lineage_columns.trace_column(
        session, asset_id, column, direction=lineage_columns.TraceDirection.UPSTREAM
    )
    ids = {asset_id, *(n.asset_id for n in trace.upstream)}
    assets = {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(ids)))}
    namespaces = {aid: str(a.namespace) for aid, a in assets.items()}
    pass_through = _pass_through_nodes(trace, namespaces)
    is_monitor = expectation_type.startswith(_MONITOR_PREFIX)
    same_type = (
        []
        if is_monitor
        else _equivalent_checks(
            session, pass_through, namespaces, expectation_type=expectation_type
        )
    )
    wanted = _params(config)
    equivalents = [(c, s, n) for c, s, n in same_type if _params(c.config) == wanted]
    levels = (
        effective_permissions(session, [s for _, s, _ in equivalents], user_id)
        if user_id is not None and equivalents
        else {}
    )
    visible = [(c, s, n) for c, s, n in equivalents if levels.get(s.id)]
    covered_nodes = {n for _, _, n in equivalents}

    def name(aid: uuid.UUID) -> str | None:
        asset = assets.get(aid)
        return str(asset.name) if asset is not None else None

    origins = []
    for origin in trace.origins:
        node = _Node(
            origin.asset_id,
            lineage_columns.fold_column(namespaces.get(origin.asset_id, ""), origin.column),
        )
        origins.append(
            {
                "asset_id": str(origin.asset_id),
                "asset_name": name(origin.asset_id),
                "column": origin.column,
                "confirmed": origin.confirmed,
                "pass_through": node in pass_through,
                "has_equivalent_check": node in covered_nodes,
            }
        )
    if is_monitor:
        recommendation: str | None = None
    elif equivalents:
        recommendation = ALREADY_COVERED_UPSTREAM
    elif any(o["pass_through"] for o in origins):
        recommendation = PLACE_AT_ORIGIN
    else:
        recommendation = None
    return {
        "upstream_status": str(trace.upstream_status),
        "complete": trace.complete,
        "origins": origins,
        "equivalent_upstream_checks": [
            {
                "asset_id": str(n.asset_id),
                "asset_name": name(n.asset_id),
                "column": check.config.get("column"),
                "suite_id": str(suite.id),
                "suite_name": suite.name,
                "check_id": str(check.id),
                "check_name": check.name,
            }
            for check, suite, n in visible
        ],
        # Equivalents on suites the requester cannot view: counted, never named (ADR 0037).
        "restricted_equivalent_checks": len(equivalents) - len(visible),
        # Same check type on a same-name upstream column, but different parameters — not coverage.
        "different_parameters_upstream": len(same_type) - len(equivalents),
        "recommendation": recommendation,
    }


def annotate_suggestions(
    session: Session,
    *,
    suite: Suite,
    user_id: uuid.UUID | None,
    suggestions: list[dict[str, Any]],
) -> None:
    """Attach a ``lineage`` annotation to every column-scoped suggestion, in place.

    Fail-soft per suggestion: advice must never cost the reviewer the suggestion itself, so a
    lineage error leaves ``lineage: {"upstream_status": "error"}`` — stated, not omitted.
    """
    if suite.asset_id is None:
        return
    for suggestion in suggestions:
        column = (suggestion.get("config") or {}).get("column")
        if not isinstance(column, str) or not column.strip():
            continue
        try:
            # A SAVEPOINT, so a failed read cannot poison the caller's transaction.
            with session.begin_nested():
                suggestion["lineage"] = placement_for(
                    session,
                    asset_id=suite.asset_id,
                    column=column,
                    expectation_type=str(suggestion.get("expectation_type")),
                    user_id=user_id,
                    config=suggestion.get("config"),
                )
        except Exception as exc:
            log.warning(
                "suggestion_lineage_placement_failed",
                suite_id=str(suite.id),
                error_type=type(exc).__name__,
            )
            suggestion["lineage"] = {"upstream_status": "error"}
