from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.column_hop_read import ColumnHopRead
    from ..models.column_node_read import ColumnNodeRead
    from ..models.column_origin_read import ColumnOriginRead
    from ..models.column_trace_asset_read import ColumnTraceAssetRead
    from ..models.coverage_gap_read import CoverageGapRead


T = TypeVar("T", bound="ColumnTraceRead")


@_attrs_define
class ColumnTraceRead:
    """Column-grain provenance (`upstream`, `origins`) and impact (`downstream`) for one column.

    `*_status` per direction: `traced` · `no_table_lineage` · `none_recorded` · `incomplete`
    (null when that direction was not requested). `complete` is false whenever `gaps` is
    non-empty or `truncated` — then an absent column is NOT evidence of no dependency (#828).
    The column's existence on the asset is not verified: a misspelling traces to nothing.

        Attributes:
            asset_id (UUID):
            assets (list[ColumnTraceAssetRead]):
            column (str):
            complete (bool):
            downstream (list[ColumnNodeRead]):
            downstream_status (None | str):
            gaps (list[CoverageGapRead]):
            hops (list[ColumnHopRead]):
            origins (list[ColumnOriginRead]):
            qualified_by (list[str]):
            truncated (bool):
            upstream (list[ColumnNodeRead]):
            upstream_status (None | str):
    """

    asset_id: UUID
    assets: list[ColumnTraceAssetRead]
    column: str
    complete: bool
    downstream: list[ColumnNodeRead]
    downstream_status: None | str
    gaps: list[CoverageGapRead]
    hops: list[ColumnHopRead]
    origins: list[ColumnOriginRead]
    qualified_by: list[str]
    truncated: bool
    upstream: list[ColumnNodeRead]
    upstream_status: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        asset_id = str(self.asset_id)

        assets = []
        for assets_item_data in self.assets:
            assets_item = assets_item_data.to_dict()
            assets.append(assets_item)

        column = self.column

        complete = self.complete

        downstream = []
        for downstream_item_data in self.downstream:
            downstream_item = downstream_item_data.to_dict()
            downstream.append(downstream_item)

        downstream_status: None | str
        downstream_status = self.downstream_status

        gaps = []
        for gaps_item_data in self.gaps:
            gaps_item = gaps_item_data.to_dict()
            gaps.append(gaps_item)

        hops = []
        for hops_item_data in self.hops:
            hops_item = hops_item_data.to_dict()
            hops.append(hops_item)

        origins = []
        for origins_item_data in self.origins:
            origins_item = origins_item_data.to_dict()
            origins.append(origins_item)

        qualified_by = self.qualified_by

        truncated = self.truncated

        upstream = []
        for upstream_item_data in self.upstream:
            upstream_item = upstream_item_data.to_dict()
            upstream.append(upstream_item)

        upstream_status: None | str
        upstream_status = self.upstream_status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "asset_id": asset_id,
                "assets": assets,
                "column": column,
                "complete": complete,
                "downstream": downstream,
                "downstream_status": downstream_status,
                "gaps": gaps,
                "hops": hops,
                "origins": origins,
                "qualified_by": qualified_by,
                "truncated": truncated,
                "upstream": upstream,
                "upstream_status": upstream_status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.column_hop_read import ColumnHopRead
        from ..models.column_node_read import ColumnNodeRead
        from ..models.column_origin_read import ColumnOriginRead
        from ..models.column_trace_asset_read import ColumnTraceAssetRead
        from ..models.coverage_gap_read import CoverageGapRead

        d = dict(src_dict)
        asset_id = UUID(d.pop("asset_id"))

        assets = []
        _assets = d.pop("assets")
        for assets_item_data in _assets:
            assets_item = ColumnTraceAssetRead.from_dict(assets_item_data)

            assets.append(assets_item)

        column = d.pop("column")

        complete = d.pop("complete")

        downstream = []
        _downstream = d.pop("downstream")
        for downstream_item_data in _downstream:
            downstream_item = ColumnNodeRead.from_dict(downstream_item_data)

            downstream.append(downstream_item)

        def _parse_downstream_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        downstream_status = _parse_downstream_status(d.pop("downstream_status"))

        gaps = []
        _gaps = d.pop("gaps")
        for gaps_item_data in _gaps:
            gaps_item = CoverageGapRead.from_dict(gaps_item_data)

            gaps.append(gaps_item)

        hops = []
        _hops = d.pop("hops")
        for hops_item_data in _hops:
            hops_item = ColumnHopRead.from_dict(hops_item_data)

            hops.append(hops_item)

        origins = []
        _origins = d.pop("origins")
        for origins_item_data in _origins:
            origins_item = ColumnOriginRead.from_dict(origins_item_data)

            origins.append(origins_item)

        qualified_by = cast(list[str], d.pop("qualified_by"))

        truncated = d.pop("truncated")

        upstream = []
        _upstream = d.pop("upstream")
        for upstream_item_data in _upstream:
            upstream_item = ColumnNodeRead.from_dict(upstream_item_data)

            upstream.append(upstream_item)

        def _parse_upstream_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        upstream_status = _parse_upstream_status(d.pop("upstream_status"))

        column_trace_read = cls(
            asset_id=asset_id,
            assets=assets,
            column=column,
            complete=complete,
            downstream=downstream,
            downstream_status=downstream_status,
            gaps=gaps,
            hops=hops,
            origins=origins,
            qualified_by=qualified_by,
            truncated=truncated,
            upstream=upstream,
            upstream_status=upstream_status,
        )

        column_trace_read.additional_properties = d
        return column_trace_read

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
