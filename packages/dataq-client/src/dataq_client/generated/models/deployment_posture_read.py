from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.deployment_posture_read_zero_sample_source import (
    DeploymentPostureReadZeroSampleSource,
)

if TYPE_CHECKING:
    from ..models.external_transfer import ExternalTransfer


T = TypeVar("T", bound="DeploymentPostureRead")


@_attrs_define
class DeploymentPostureRead:
    """What an auditor needs to answer "where does our data live, and what can
    take it elsewhere?" without shell access to the deployment.

        Attributes:
            external_transfers (list[ExternalTransfer]):
            region (None | str):
            zero_sample_mode (bool):
            zero_sample_source (DeploymentPostureReadZeroSampleSource):
    """

    external_transfers: list[ExternalTransfer]
    region: None | str
    zero_sample_mode: bool
    zero_sample_source: DeploymentPostureReadZeroSampleSource
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        external_transfers = []
        for external_transfers_item_data in self.external_transfers:
            external_transfers_item = external_transfers_item_data.to_dict()
            external_transfers.append(external_transfers_item)

        region: None | str
        region = self.region

        zero_sample_mode = self.zero_sample_mode

        zero_sample_source = self.zero_sample_source.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "external_transfers": external_transfers,
                "region": region,
                "zero_sample_mode": zero_sample_mode,
                "zero_sample_source": zero_sample_source,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.external_transfer import ExternalTransfer

        d = dict(src_dict)
        external_transfers = []
        _external_transfers = d.pop("external_transfers")
        for external_transfers_item_data in _external_transfers:
            external_transfers_item = ExternalTransfer.from_dict(external_transfers_item_data)

            external_transfers.append(external_transfers_item)

        def _parse_region(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        region = _parse_region(d.pop("region"))

        zero_sample_mode = d.pop("zero_sample_mode")

        zero_sample_source = DeploymentPostureReadZeroSampleSource(d.pop("zero_sample_source"))

        deployment_posture_read = cls(
            external_transfers=external_transfers,
            region=region,
            zero_sample_mode=zero_sample_mode,
            zero_sample_source=zero_sample_source,
        )

        deployment_posture_read.additional_properties = d
        return deployment_posture_read

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
