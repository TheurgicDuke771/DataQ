"""The ADR 0037 three-layer visibility contract, pinned across every surface.

This file used to pin the *redaction* regime (#845/#920: anonymous lineage nodes,
redacted browse rows, 404-no-leak asset detail). ADR 0037 deliberately superseded
that: asset identity and lineage topology are workspace knowledge, aggregate
verdicts are workspace-true, and the grant boundary lives at the suite grain. The
tests here pin the NEW rule with the same rigor the old ones pinned the old one:

1. **Identity is public.** Every member sees every asset fully named — in browse,
   in the detail endpoint (which opens for every existing asset), and in the
   lineage graph (nodes named, column pairs included).
2. **Aggregates are workspace-true.** The summary a non-grantee sees is byte-for-
   byte the summary the admin sees — one verdict per asset, never a per-viewer
   partial.
3. **Items are granted.** The composing-suite list on detail carries only suites
   the caller can view; the rest surface as ``restricted_suite_count`` — a count,
   never names. (Suite/run/result endpoints keep their own 404-no-leak tests.)
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, cast

import pytest
from sqlalchemy import select

from backend.app.db.models import Asset, LineageEdge, Suite, User
from backend.app.services import asset_view_service as svc
from backend.app.services import share_service, suite_service
from backend.app.services.asset_view_service import AssetNotFoundError
from backend.tests.services.test_asset_view_service import _conn, _user

_SECRET_NAME = "MART_ORDER_REVENUE"
_SECRET_NS = "snowflake://acme-secret-account"


def _grant(db: Any, suite: Suite, owner: User, target: User) -> None:
    """Give ``target`` view on ``suite`` (the ADR-0027 share ladder)."""
    share_service.grant_share(
        db, suite.id, actor_id=owner.id, target_user_id=target.id, permission="view"
    )
    db.commit()


def _suite_on(db: Any, owner: User, *, table: str) -> Suite:
    conn = _conn(db, owner)
    suite = suite_service.create_suite(
        db,
        name=f"S-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )
    db.commit()
    assert suite.asset_id is not None
    return suite


def _ungranted_downstream(db: Any, upstream_asset_id: uuid.UUID) -> Asset:
    """A real asset downstream of ``upstream_asset_id``, targeted by a suite the
    viewer holds no grant on — the shape the old redaction regime anonymized."""
    stranger = _user(db)
    mart_suite = _suite_on(db, stranger, table=_SECRET_NAME)
    mart = cast(Asset, db.get(Asset, mart_suite.asset_id))
    mart.namespace = _SECRET_NS
    mart.name = _SECRET_NAME
    db.add(
        LineageEdge(
            upstream_asset_id=upstream_asset_id,
            downstream_asset_id=mart.id,
            source="dbt",
            columns=[["order_total", "revenue"]],
        )
    )
    db.commit()
    return mart


@pytest.fixture
def scenario(db_session: Any) -> tuple[User, Asset, Asset]:
    """Olivia can view ORDER_HEADER (shared with her). A mart she has no grant on
    sits downstream of it."""
    owner = _user(db_session)
    olivia = _user(db_session)
    header_suite = _suite_on(db_session, owner, table="ORDER_HEADER")
    _grant(db_session, header_suite, owner, olivia)
    header = cast(Asset, db_session.get(Asset, header_suite.asset_id))
    mart = _ungranted_downstream(db_session, header.id)
    return olivia, header, mart


def test_an_ungranted_neighbour_is_fully_named(db_session: Any, scenario: Any) -> None:
    """Layer 1: lineage topology is identity, and identity is workspace knowledge.
    The node arrives named, placed, and honestly flagged as monitored."""
    olivia, header, mart = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    assert len(detail.downstream) == 1
    node = detail.downstream[0]
    assert node.id == mart.id
    assert node.name == _SECRET_NAME
    assert node.namespace == _SECRET_NS
    assert node.is_monitored is True  # the true structural fact, not forced False


def test_an_ungranted_asset_detail_opens(db_session: Any, scenario: Any) -> None:
    """The asset-grain 404-no-leak is retired: every node the graph draws is a node
    the endpoint opens — for every member. Only a truly unknown id 404s."""
    olivia, _, mart = scenario
    detail = svc.get_visible_asset(db_session, mart.id, user_id=olivia.id)
    assert detail.summary.name == _SECRET_NAME

    with pytest.raises(AssetNotFoundError):
        svc.get_visible_asset(db_session, uuid.uuid4(), user_id=olivia.id)


def test_column_pairs_are_shown_to_every_member(db_session: Any, scenario: Any) -> None:
    """Column names are schema metadata — identity, not measurement. The pairs on
    an edge to an ungranted asset arrive in full (the #901 count-only box retired)."""
    olivia, header, mart = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    edge = next(e for e in detail.lineage_edges if (e.source, e.target) == (header.id, mart.id))
    assert edge.columns == (("order_total", "revenue"),)


def test_the_suite_boundary_holds_on_detail(db_session: Any, scenario: Any) -> None:
    """Layer 3: itemized evaluation stays granted. Olivia opens the mart, but its
    composing suite — which she cannot view — is a count, never a name."""
    olivia, _, mart = scenario
    detail = svc.get_visible_asset(db_session, mart.id, user_id=olivia.id)
    assert detail.suites == []
    assert detail.restricted_suite_count == 1
    # The workspace-true summary still counts the suite she cannot list.
    assert detail.summary.suite_count == 1


def test_the_summary_is_workspace_true(db_session: Any, scenario: Any) -> None:
    """Layer 2: one verdict per asset. A non-grantee, a grantee, and an admin all
    compute byte-identical summaries — a per-viewer partial can never disagree."""
    olivia, _, mart = scenario
    admin = _user(db_session)
    for_olivia = svc.get_visible_asset(db_session, mart.id, user_id=olivia.id).summary
    for_admin = svc.get_visible_asset(
        db_session, mart.id, user_id=admin.id, include_all=True
    ).summary
    assert dataclasses.asdict(for_olivia) == dataclasses.asdict(for_admin)


def test_an_admin_sees_the_suite_list_in_full(
    db_session: Any, scenario: Any, make_workspace_admin: Any
) -> None:
    """`include_all` (ADR 0027 workspace-admin) lists every composing suite, so
    nothing collapses into the restricted count. The user must be a REAL
    workspace-admin: `effective_permissions` resolves the `admin` label from the
    allowlist independently of `include_all`."""
    _, _, mart = scenario
    admin = _user(db_session)
    make_workspace_admin(admin.email)
    detail = svc.get_visible_asset(db_session, mart.id, user_id=admin.id, include_all=True)
    assert len(detail.suites) == 1
    assert detail.suites[0].my_permission == "admin"
    assert detail.restricted_suite_count == 0


def test_a_grantee_sees_their_suite_listed(db_session: Any, scenario: Any) -> None:
    """The visible/restricted split follows the grant, per suite: granting Olivia
    the mart suite moves it from the count into the named list."""
    olivia, _, mart = scenario
    mart_suite = db_session.scalars(select(Suite).where(Suite.asset_id == mart.id)).one()
    _grant(db_session, mart_suite, db_session.get(User, mart_suite.created_by), olivia)
    detail = svc.get_visible_asset(db_session, mart.id, user_id=olivia.id)
    assert [s.suite_id for s in detail.suites] == [mart_suite.id]
    assert detail.restricted_suite_count == 0


def test_the_three_surfaces_agree_identity_is_public(db_session: Any) -> None:
    """Browse, the detail endpoint, and the lineage nodes must all present the SAME
    identity to every caller — over every asset kind (granted / ungranted /
    suite-less). This is the ADR 0037 successor of the old three-surfaces test: the
    axis is no longer per-viewer accessibility (there is none at the asset grain)
    but that no surface withholds or invents identity for any caller."""
    owner = _user(db_session)
    viewer = _user(db_session)
    stranger = _user(db_session)

    granted = _suite_on(db_session, owner, table="GRANTED")
    _grant(db_session, granted, owner, viewer)
    ungranted = _suite_on(db_session, stranger, table="UNGRANTED")
    suiteless = Asset(namespace=_SECRET_NS, name="SUITELESS", env="dev")
    db_session.add(suiteless)
    db_session.commit()

    kinds = {
        "granted": cast(uuid.UUID, granted.asset_id),
        "ungranted": cast(uuid.UUID, ungranted.asset_id),
        "suiteless": suiteless.id,
    }
    expected_visible_suites = {"granted": 1, "ungranted": 0, "suiteless": 0}
    expected_restricted = {"granted": 0, "ungranted": 1, "suiteless": 0}

    rows_by_id = {row.id: row for row in svc.list_visible_assets(db_session)}
    for kind, asset_id in kinds.items():
        # 1) browse: present, named, with the workspace-true suite count.
        row = rows_by_id.get(asset_id)
        assert row is not None, f"{kind}: browse omitted the asset"
        assert row.name, f"{kind}: browse withheld the name"
        # 2) detail: opens for the plain member; identity matches browse; the
        #    itemized split follows the grants.
        detail = svc.get_visible_asset(db_session, asset_id, user_id=viewer.id)
        assert detail.summary.name == row.name
        assert detail.summary.suite_count == row.suite_count
        assert len(detail.suites) == expected_visible_suites[kind], kind
        assert detail.restricted_suite_count == expected_restricted[kind], kind


def test_browse_lists_every_asset_for_a_plain_member(db_session: Any, scenario: Any) -> None:
    """The #920 redacted browse row is gone from the contract: a member with zero
    grants on the mart still gets its full row, with the workspace-true rollup."""
    _, _, mart = scenario
    rows = {r.id: r for r in svc.list_visible_assets(db_session)}
    row = rows[mart.id]
    assert row.name == _SECRET_NAME
    assert row.namespace == _SECRET_NS
    assert row.suite_count == 1
