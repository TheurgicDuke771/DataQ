from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.incident_narrative_read_narrative_type_0 import (
        IncidentNarrativeReadNarrativeType0,
    )


T = TypeVar("T", bound="IncidentNarrativeRead")


@_attrs_define
class IncidentNarrativeRead:
    """The latest root-cause narrative for an incident (#1633), as the UI reads it.

    `narrative` is null both when none was ever generated (the common case — RCA
    is on demand) and when one exists but is withheld from this caller; the two
    are told apart by `withheld_reason`, so a client never renders "no narrative"
    over one it simply may not read.

        Attributes:
            generated_at (datetime.datetime | None):
            invocation_id (None | UUID):
            narrative (IncidentNarrativeReadNarrativeType0 | None):
            withheld_reason (None | str):
    """

    generated_at: datetime.datetime | None
    invocation_id: None | UUID
    narrative: IncidentNarrativeReadNarrativeType0 | None
    withheld_reason: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.incident_narrative_read_narrative_type_0 import (
            IncidentNarrativeReadNarrativeType0,
        )

        generated_at: None | str
        if isinstance(self.generated_at, datetime.datetime):
            generated_at = self.generated_at.isoformat()
        else:
            generated_at = self.generated_at

        invocation_id: None | str
        if isinstance(self.invocation_id, UUID):
            invocation_id = str(self.invocation_id)
        else:
            invocation_id = self.invocation_id

        narrative: dict[str, Any] | None
        if isinstance(self.narrative, IncidentNarrativeReadNarrativeType0):
            narrative = self.narrative.to_dict()
        else:
            narrative = self.narrative

        withheld_reason: None | str
        withheld_reason = self.withheld_reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "generated_at": generated_at,
                "invocation_id": invocation_id,
                "narrative": narrative,
                "withheld_reason": withheld_reason,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.incident_narrative_read_narrative_type_0 import (
            IncidentNarrativeReadNarrativeType0,
        )

        d = dict(src_dict)

        def _parse_generated_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                generated_at_type_0 = datetime.datetime.fromisoformat(data)

                return generated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        generated_at = _parse_generated_at(d.pop("generated_at"))

        def _parse_invocation_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                invocation_id_type_0 = UUID(data)

                return invocation_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        invocation_id = _parse_invocation_id(d.pop("invocation_id"))

        def _parse_narrative(data: object) -> IncidentNarrativeReadNarrativeType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                narrative_type_0 = IncidentNarrativeReadNarrativeType0.from_dict(data)

                return narrative_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(IncidentNarrativeReadNarrativeType0 | None, data)

        narrative = _parse_narrative(d.pop("narrative"))

        def _parse_withheld_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        withheld_reason = _parse_withheld_reason(d.pop("withheld_reason"))

        incident_narrative_read = cls(
            generated_at=generated_at,
            invocation_id=invocation_id,
            narrative=narrative,
            withheld_reason=withheld_reason,
        )

        incident_narrative_read.additional_properties = d
        return incident_narrative_read

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
