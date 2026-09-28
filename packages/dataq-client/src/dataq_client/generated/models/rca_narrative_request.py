from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="RcaNarrativeRequest")


@_attrs_define
class RcaNarrativeRequest:
    """
    Attributes:
        incident_id (UUID):
    """

    incident_id: UUID

    def to_dict(self) -> dict[str, Any]:
        incident_id = str(self.incident_id)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "incident_id": incident_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        incident_id = UUID(d.pop("incident_id"))

        rca_narrative_request = cls(
            incident_id=incident_id,
        )

        return rca_narrative_request
