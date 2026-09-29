from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.asset_summary_read import AssetSummaryRead
    from ..models.composing_suite_read import ComposingSuiteRead
    from ..models.inherited_classification_read import InheritedClassificationRead
    from ..models.lineage_edge_read import LineageEdgeRead
    from ..models.lineage_node_read import LineageNodeRead
    from ..models.lineage_source_health_read import LineageSourceHealthRead
    from ..models.scorecard_read import ScorecardRead
    from ..models.warehouse_lineage_status_read import WarehouseLineageStatusRead


T = TypeVar("T", bound="AssetDetailRead")


@_attrs_define
class AssetDetailRead:
    """Asset detail: the workspace-true summary + the caller's per-suite breakdown
    + upstream/downstream lineage. `suites` lists only suites the caller can view
    (ADR 0027); `restricted_suite_count` is how many more compose the asset — they
    roll into `summary` (workspace-true) but stay unnamed.

        Attributes:
            downstream (list[LineageNodeRead]):
            lineage_edges (list[LineageEdgeRead]):
            suites (list[ComposingSuiteRead]):
            summary (AssetSummaryRead): List-row aggregation for one asset — **workspace-true** (ADR 0037): every
                field is identical for every viewer, aggregated over ALL composing suites.
                Carries **two orthogonal health axes** (#803) the UI renders separately:
            upstream (list[LineageNodeRead]):
            failing_lineage_sources (list[LineageSourceHealthRead] | Unset):
            inherited_classifications (list[InheritedClassificationRead] | None | Unset):
            inherited_classifications_truncated (bool | Unset):  Default: False.
            restricted_suite_count (int | Unset):  Default: 0.
            scorecard (None | ScorecardRead | Unset):
            warehouse_lineage_status (list[WarehouseLineageStatusRead] | Unset):
    """

    downstream: list[LineageNodeRead]
    lineage_edges: list[LineageEdgeRead]
    suites: list[ComposingSuiteRead]
    summary: AssetSummaryRead
    upstream: list[LineageNodeRead]
    failing_lineage_sources: list[LineageSourceHealthRead] | Unset = UNSET
    inherited_classifications: list[InheritedClassificationRead] | None | Unset = UNSET
    inherited_classifications_truncated: bool | Unset = False
    restricted_suite_count: int | Unset = 0
    scorecard: None | ScorecardRead | Unset = UNSET
    warehouse_lineage_status: list[WarehouseLineageStatusRead] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.scorecard_read import ScorecardRead

        downstream = []
        for downstream_item_data in self.downstream:
            downstream_item = downstream_item_data.to_dict()
            downstream.append(downstream_item)

        lineage_edges = []
        for lineage_edges_item_data in self.lineage_edges:
            lineage_edges_item = lineage_edges_item_data.to_dict()
            lineage_edges.append(lineage_edges_item)

        suites = []
        for suites_item_data in self.suites:
            suites_item = suites_item_data.to_dict()
            suites.append(suites_item)

        summary = self.summary.to_dict()

        upstream = []
        for upstream_item_data in self.upstream:
            upstream_item = upstream_item_data.to_dict()
            upstream.append(upstream_item)

        failing_lineage_sources: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.failing_lineage_sources, Unset):
            failing_lineage_sources = []
            for failing_lineage_sources_item_data in self.failing_lineage_sources:
                failing_lineage_sources_item = failing_lineage_sources_item_data.to_dict()
                failing_lineage_sources.append(failing_lineage_sources_item)

        inherited_classifications: list[dict[str, Any]] | None | Unset
        if isinstance(self.inherited_classifications, Unset):
            inherited_classifications = UNSET
        elif isinstance(self.inherited_classifications, list):
            inherited_classifications = []
            for inherited_classifications_type_0_item_data in self.inherited_classifications:
                inherited_classifications_type_0_item = (
                    inherited_classifications_type_0_item_data.to_dict()
                )
                inherited_classifications.append(inherited_classifications_type_0_item)

        else:
            inherited_classifications = self.inherited_classifications

        inherited_classifications_truncated = self.inherited_classifications_truncated

        restricted_suite_count = self.restricted_suite_count

        scorecard: dict[str, Any] | None | Unset
        if isinstance(self.scorecard, Unset):
            scorecard = UNSET
        elif isinstance(self.scorecard, ScorecardRead):
            scorecard = self.scorecard.to_dict()
        else:
            scorecard = self.scorecard

        warehouse_lineage_status: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.warehouse_lineage_status, Unset):
            warehouse_lineage_status = []
            for warehouse_lineage_status_item_data in self.warehouse_lineage_status:
                warehouse_lineage_status_item = warehouse_lineage_status_item_data.to_dict()
                warehouse_lineage_status.append(warehouse_lineage_status_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "downstream": downstream,
                "lineage_edges": lineage_edges,
                "suites": suites,
                "summary": summary,
                "upstream": upstream,
            }
        )
        if failing_lineage_sources is not UNSET:
            field_dict["failing_lineage_sources"] = failing_lineage_sources
        if inherited_classifications is not UNSET:
            field_dict["inherited_classifications"] = inherited_classifications
        if inherited_classifications_truncated is not UNSET:
            field_dict["inherited_classifications_truncated"] = inherited_classifications_truncated
        if restricted_suite_count is not UNSET:
            field_dict["restricted_suite_count"] = restricted_suite_count
        if scorecard is not UNSET:
            field_dict["scorecard"] = scorecard
        if warehouse_lineage_status is not UNSET:
            field_dict["warehouse_lineage_status"] = warehouse_lineage_status

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.asset_summary_read import AssetSummaryRead
        from ..models.composing_suite_read import ComposingSuiteRead
        from ..models.inherited_classification_read import (
            InheritedClassificationRead,
        )
        from ..models.lineage_edge_read import LineageEdgeRead
        from ..models.lineage_node_read import LineageNodeRead
        from ..models.lineage_source_health_read import LineageSourceHealthRead
        from ..models.scorecard_read import ScorecardRead
        from ..models.warehouse_lineage_status_read import (
            WarehouseLineageStatusRead,
        )

        d = dict(src_dict)
        downstream = []
        _downstream = d.pop("downstream")
        for downstream_item_data in _downstream:
            downstream_item = LineageNodeRead.from_dict(downstream_item_data)

            downstream.append(downstream_item)

        lineage_edges = []
        _lineage_edges = d.pop("lineage_edges")
        for lineage_edges_item_data in _lineage_edges:
            lineage_edges_item = LineageEdgeRead.from_dict(lineage_edges_item_data)

            lineage_edges.append(lineage_edges_item)

        suites = []
        _suites = d.pop("suites")
        for suites_item_data in _suites:
            suites_item = ComposingSuiteRead.from_dict(suites_item_data)

            suites.append(suites_item)

        summary = AssetSummaryRead.from_dict(d.pop("summary"))

        upstream = []
        _upstream = d.pop("upstream")
        for upstream_item_data in _upstream:
            upstream_item = LineageNodeRead.from_dict(upstream_item_data)

            upstream.append(upstream_item)

        _failing_lineage_sources = d.pop("failing_lineage_sources", UNSET)
        failing_lineage_sources: list[LineageSourceHealthRead] | Unset = UNSET
        if _failing_lineage_sources is not UNSET:
            failing_lineage_sources = []
            for failing_lineage_sources_item_data in _failing_lineage_sources:
                failing_lineage_sources_item = LineageSourceHealthRead.from_dict(
                    failing_lineage_sources_item_data
                )

                failing_lineage_sources.append(failing_lineage_sources_item)

        def _parse_inherited_classifications(
            data: object,
        ) -> list[InheritedClassificationRead] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                inherited_classifications_type_0 = []
                _inherited_classifications_type_0 = data
                for inherited_classifications_type_0_item_data in _inherited_classifications_type_0:
                    inherited_classifications_type_0_item = InheritedClassificationRead.from_dict(
                        inherited_classifications_type_0_item_data
                    )

                    inherited_classifications_type_0.append(inherited_classifications_type_0_item)

                return inherited_classifications_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[InheritedClassificationRead] | None | Unset, data)

        inherited_classifications = _parse_inherited_classifications(
            d.pop("inherited_classifications", UNSET)
        )

        inherited_classifications_truncated = d.pop("inherited_classifications_truncated", UNSET)

        restricted_suite_count = d.pop("restricted_suite_count", UNSET)

        def _parse_scorecard(data: object) -> None | ScorecardRead | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                scorecard_type_0 = ScorecardRead.from_dict(data)

                return scorecard_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ScorecardRead | Unset, data)

        scorecard = _parse_scorecard(d.pop("scorecard", UNSET))

        _warehouse_lineage_status = d.pop("warehouse_lineage_status", UNSET)
        warehouse_lineage_status: list[WarehouseLineageStatusRead] | Unset = UNSET
        if _warehouse_lineage_status is not UNSET:
            warehouse_lineage_status = []
            for warehouse_lineage_status_item_data in _warehouse_lineage_status:
                warehouse_lineage_status_item = WarehouseLineageStatusRead.from_dict(
                    warehouse_lineage_status_item_data
                )

                warehouse_lineage_status.append(warehouse_lineage_status_item)

        asset_detail_read = cls(
            downstream=downstream,
            lineage_edges=lineage_edges,
            suites=suites,
            summary=summary,
            upstream=upstream,
            failing_lineage_sources=failing_lineage_sources,
            inherited_classifications=inherited_classifications,
            inherited_classifications_truncated=inherited_classifications_truncated,
            restricted_suite_count=restricted_suite_count,
            scorecard=scorecard,
            warehouse_lineage_status=warehouse_lineage_status,
        )

        asset_detail_read.additional_properties = d
        return asset_detail_read

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
