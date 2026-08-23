"""Cross-producer OpenLineage identity alignment (#823, ADR 0034 §6)."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.lineage.identity import canonical_identity
from backend.app.lineage.marquez import _parse_graph
from backend.app.services.asset_identity import format_snowflake_name

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "lineage"
_NS = "snowflake://ACME-TEST01"


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _catalog_names() -> list[str]:
    return [d["name"] for d in _load("marquez_datasets_dbt_real.json")["datasets"]]


class TestTheBugItself:
    """The mismatch, asserted against the real payload — so it can never silently return."""

    def test_a_real_producer_does_not_emit_our_casing(self) -> None:
        # DataQ's identity for the dbt mart, straight from the real resolver.
        ours = format_snowflake_name("DATAQ_DB", "ANALYTICS", "mart_order_revenue")
        assert ours == "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE"

        # What openlineage-dbt actually put in the catalog.
        names = _catalog_names()
        assert "DATAQ_DB.ANALYTICS.mart_order_revenue" in names

        # The whole bug in one line: the name DataQ would seed with is NOT in the
        # catalog, so the seed 404s against a perfectly-populated one.
        assert ours not in names

    def test_not_one_real_dataset_matches_a_dataq_identity(self) -> None:
        # The catalog was populated by the real producer alone.
        assert [n for n in _catalog_names() if n == n.upper()] == []

    def test_the_real_name_is_neither_upper_nor_lower(self) -> None:
        # This is why "just lowercase it" (or "try both cases") cannot work: the real name is MIXED
        # — db/schema come from the dbt profile (upper), the table from the model filename (lower).
        name = "DATAQ_DB.ANALYTICS.mart_order_revenue"
        assert name in _catalog_names()
        assert name != name.upper()
        assert name != name.lower()


class TestCanonicalIdentityReconciles:
    def test_our_name_and_the_real_producers_name_fold_to_one_key(self) -> None:
        ours = format_snowflake_name("DATAQ_DB", "ANALYTICS", "mart_order_revenue")
        theirs = "DATAQ_DB.ANALYTICS.mart_order_revenue"
        assert ours != theirs  # the premise ADR 0034 got wrong
        assert canonical_identity(_NS, ours) == canonical_identity(_NS, theirs)

    def test_every_real_catalog_dataset_folds_onto_a_dataq_identity(self) -> None:
        # The end-to-end claim: for every table the real producer emitted, DataQ's own
        # resolver and the catalog agree once folded. If this fails, the pull is dark.
        for name in _catalog_names():
            database, schema, table = name.split(".")
            ours = format_snowflake_name(database, schema, table)
            assert canonical_identity(_NS, ours) == canonical_identity(_NS, name), name

    def test_folding_is_engine_correct_not_a_blanket_upper(self) -> None:
        assert canonical_identity("snowflake://a", "db.s.t")[1] == "DB.S.T"
        assert canonical_identity("unitycatalog://h", "CAT.SCH.TBL")[1] == "cat.sch.tbl"

    @pytest.mark.parametrize(
        "namespace",
        [
            "abfss://raw@acct.dfs.core.windows.net",
            "s3://bucket",
            "postgresql+psycopg2://host/iceberg_catalog",  # Iceberg
        ],
    )
    def test_case_sensitive_stores_are_never_folded(self, namespace: str) -> None:
        # Load-bearing.
        assert canonical_identity(namespace, "raw/Orders.csv")[1] == "raw/Orders.csv"
        assert canonical_identity(namespace, "raw/Orders.csv") != canonical_identity(
            namespace, "raw/orders.csv"
        )


class TestTheRealLineageGraphParses:
    def test_the_captured_marquez_graph_yields_the_dbt_chain(self) -> None:
        payload = _load("marquez_lineage_dbt_real.json")
        graph = _parse_graph(
            payload, seed_node_id=f"dataset:{_NS}:DATAQ_DB.ANALYTICS.mart_order_revenue"
        )

        datasets = {n.name for n in graph.nodes.values() if n.namespace}
        # The real dbt lineage: RETAIL sources -> ANALYTICS_STG staging -> the mart.
        assert "DATAQ_DB.RETAIL.orders_header" in datasets
        assert "DATAQ_DB.ANALYTICS_STG.stg_orders" in datasets
        assert "DATAQ_DB.ANALYTICS.mart_order_revenue" in datasets
        assert graph.edges, "the real payload must carry edges"

    def test_folding_the_real_graph_lands_on_dataq_asset_identities(self) -> None:
        # What the pull now does on ingest: every catalog identity is canonicalized.
        payload = _load("marquez_lineage_dbt_real.json")
        graph = _parse_graph(payload, seed_node_id="dataset:x:y")
        folded = {
            canonical_identity(n.namespace, n.name)[1]
            for n in graph.nodes.values()
            if n.namespace and n.name
        }
        assert "DATAQ_DB.RETAIL.ORDERS_HEADER" in folded
        assert "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE" in folded
        # and no lower-cased twin survives to fork an asset
        assert not any(f != f.upper() for f in folded)


class TestThePullResolvesAgainstTheRealCatalog:
    """End-to-end, against a real Postgres: the #823 AC-1 — a DataQ seed resolves."""

    def test_seeds_resolve_and_edges_land_on_dataq_assets(self, db_session: Any) -> None:
        from backend.app.db.models import Asset, LineageEdge
        from backend.app.lineage.pull import refresh_pulled_edges

        # DataQ's OWN identity for two tables it monitors — upper-cased, as the engine
        # reports them. Nothing here is bent to match the catalog.
        for table in ("ORDERS_HEADER", "ORDER_LINES"):
            db_session.add(Asset(namespace=_NS, name=f"DATAQ_DB.RETAIL.{table}", env="dev"))
        db_session.commit()

        provider = _ReplayProvider()
        live = refresh_pulled_edges(db_session, provider=provider)

        # AC-1: fetched_pairs > 0 — the seed resolved against a real-producer catalog.
        assert live is not None and live > 0, "the pull is dark — seeds did not resolve"

        # It seeded with the CATALOG's names (lower), not ours (upper).
        seeded = {name for (_ns, name, _d) in provider.calls}
        assert "DATAQ_DB.RETAIL.orders_header" in seeded
        assert "DATAQ_DB.RETAIL.ORDERS_HEADER" not in seeded

        # And the edges landed on DataQ's canonical assets — the pull did NOT fork a
        # second, lower-cased asset for a table we already knew.
        names = {a.name for a in db_session.scalars(select(Asset)).all()}
        assert "DATAQ_DB.RETAIL.ORDERS_HEADER" in names
        assert "DATAQ_DB.RETAIL.orders_header" not in names
        assert all(n == n.upper() for n in names), names

        assert db_session.scalar(
            select(func.count()).select_from(LineageEdge).where(LineageEdge.source == "marquez")
        )

    def test_an_asset_the_catalog_never_heard_of_is_absent_not_unavailable(
        self, db_session: Any
    ) -> None:
        """The #823 AC-3 signal: 'catalog doesn't know it' ≠ 'catalog is down'."""
        from backend.app.db.models import Asset
        from backend.app.lineage.pull import _collect_dataset_edges

        db_session.add(Asset(namespace=_NS, name="DATAQ_DB.RETAIL.NOT_IN_CATALOG", env="dev"))
        db_session.commit()

        _pairs, outcome = _collect_dataset_edges(
            _ReplayProvider(), [(_NS, "DATAQ_DB.RETAIL.NOT_IN_CATALOG")], depth=3
        )
        assert outcome.absent == 1
        assert outcome.unavailable == 0  # NOT an outage — do not prune on this
        assert outcome.resolved == 0

    def test_every_fold_equivalent_name_is_seeded_including_our_own_twin(self) -> None:
        """The review's #1 finding: picking ONE name is a trap."""
        from backend.app.lineage.provider import LineageGraph
        from backend.app.lineage.pull import _collect_dataset_edges

        class _CatalogWithBothCasings:
            provider = "marquez"

            def __init__(self) -> None:
                self.pulled: list[str] = []

            def list_datasets(self, *, namespace: str) -> list[str]:
                # ORDERS = what DataQ's own emitter wrote; orders = what dbt wrote.
                return ["DB.S.ORDERS", "DB.S.orders"]

            def get_lineage(self, *, namespace: str, name: str, depth: int) -> Any:
                self.pulled.append(name)
                return LineageGraph.empty()

        catalog = _CatalogWithBothCasings()
        _pairs, outcome = _collect_dataset_edges(
            catalog, [("snowflake://a", "DB.S.ORDERS")], depth=3
        )
        assert sorted(catalog.pulled) == ["DB.S.ORDERS", "DB.S.orders"]
        assert outcome.resolved == 1  # one ASSET resolved, via two catalog names
        assert outcome.absent == 0

    def test_a_name_shared_by_two_assets_is_fetched_once(self) -> None:
        from backend.app.lineage.provider import LineageGraph
        from backend.app.lineage.pull import _collect_dataset_edges

        class _Catalog:
            provider = "marquez"

            def __init__(self) -> None:
                self.pulled: list[str] = []

            def list_datasets(self, *, namespace: str) -> list[str]:
                return ["DB.S.orders"]

            def get_lineage(self, *, namespace: str, name: str, depth: int) -> Any:
                self.pulled.append(name)
                return LineageGraph.empty()

        catalog = _Catalog()
        # Two assets folding to the same key must not double the HTTP cost.
        _collect_dataset_edges(
            catalog, [("snowflake://a", "DB.S.ORDERS"), ("snowflake://a", "DB.S.orders")], depth=3
        )
        assert catalog.pulled == ["DB.S.orders"]


class TestIngestNeverMisattributesLineage:
    """The review's #4 finding: a blanket ingest fold hangs a QUOTED table's lineage on
    its unquoted twin — a silently wrong edge, which is worse than a missing one.
    """

    def test_a_quoted_identifier_keeps_its_own_asset(self) -> None:
        from backend.app.lineage.pull import _identity_resolver

        # `DB.S."orders"` (quoted) legitimately yields the asset name `DB.S.orders`.
        resolve = _identity_resolver([("snowflake://a", "DB.S.orders")])
        # A pulled `DB.S.orders` must land on THAT asset, not be folded to DB.S.ORDERS.
        assert resolve(("snowflake://a", "DB.S.orders")) == ("snowflake://a", "DB.S.orders")

    def test_when_both_twins_exist_the_catalog_name_is_kept_verbatim(self) -> None:
        from backend.app.lineage.pull import _identity_resolver

        # The quoted and unquoted tables genuinely coexist as two DataQ assets.
        resolve = _identity_resolver(
            [("snowflake://a", "DB.S.orders"), ("snowflake://a", "DB.S.ORDERS")]
        )
        # We cannot know which the catalog meant, so we must not guess: keep it verbatim.
        assert resolve(("snowflake://a", "DB.S.Orders")) == ("snowflake://a", "DB.S.Orders")

    def test_a_producers_casing_lands_on_the_asset_we_already_have(self) -> None:
        from backend.app.lineage.pull import _identity_resolver

        resolve = _identity_resolver([(_NS, "DATAQ_DB.RETAIL.ORDERS_HEADER")])
        assert resolve((_NS, "DATAQ_DB.RETAIL.orders_header")) == (
            _NS,
            "DATAQ_DB.RETAIL.ORDERS_HEADER",
        )

    def test_an_unknown_table_is_stored_canonically_so_it_converges_later(self) -> None:
        from backend.app.lineage.pull import _identity_resolver

        # A blast-radius table nobody monitors.
        resolve = _identity_resolver([])
        assert resolve((_NS, "DATAQ_DB.ANALYTICS.mart_customer_orders")) == (
            _NS,
            "DATAQ_DB.ANALYTICS.MART_CUSTOMER_ORDERS",
        )


class _ReplayProvider:
    """Replays the CAPTURED REAL Marquez responses (bytes-for-bytes what it returned)."""

    provider = "marquez"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self._graph = _load("marquez_lineage_dbt_real.json")

    def list_datasets(self, *, namespace: str) -> list[str]:
        return _catalog_names() if namespace == _NS else []

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> Any:
        self.calls.append((namespace, name, depth))
        return _parse_graph(self._graph, seed_node_id=f"dataset:{namespace}:{name}")


class TestAMismatchMustNeverDeleteTheCache:
    """The prune is the only destructive path here, and #823 nearly armed it."""

    def test_a_catalog_that_knows_none_of_our_assets_does_not_prune(self, db_session: Any) -> None:
        from backend.app.db.models import Asset, LineageEdge
        from backend.app.lineage.pull import refresh_pulled_edges

        # A previously-pulled edge sitting in the cache.
        up = Asset(namespace=_NS, name="DB.S.A", env="dev")
        down = Asset(namespace=_NS, name="DB.S.B", env="dev")
        db_session.add_all([up, down])
        db_session.flush()
        edge = LineageEdge(
            upstream_asset_id=up.id,
            downstream_asset_id=down.id,
            source="marquez",
            connection_id=None,
        )
        db_session.add(edge)
        db_session.commit()
        edge_id = edge.id

        class _CatalogKnowsNothingOfOurs:
            """Up, healthy, and holding datasets — just not ours (the #823 shape)."""

            provider = "marquez"

            def list_datasets(self, *, namespace: str) -> list[str]:
                return ["SOME_OTHER_DB.X.Y"]

            def get_lineage(self, *, namespace: str, name: str, depth: int) -> Any:
                raise AssertionError("nothing of ours should have resolved")

        refresh_pulled_edges(db_session, provider=_CatalogKnowsNothingOfOurs())

        # The cache MUST survive. If this fails, a naming mismatch silently destroys
        # every lineage edge the product has.
        assert db_session.get(LineageEdge, edge_id) is not None
