from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.audit_event_read_after_type_0 import AuditEventReadAfterType0
    from ..models.audit_event_read_before_type_0 import AuditEventReadBeforeType0


T = TypeVar("T", bound="AuditEventRead")


@_attrs_define
class AuditEventRead:
    """One audit event.

    Attributes:
        action (str):
        action_class (str):
        actor_display (None | str):
        actor_kind (str):
        actor_label (None | str):
        actor_user_id (None | str):
        after (AuditEventReadAfterType0 | None):
        before (AuditEventReadBeforeType0 | None):
        entity_id (None | str):
        entity_type (str):
        id (str):
        occurred_at (str):
        request_id (None | str):
    """

    action: str
    action_class: str
    actor_display: None | str
    actor_kind: str
    actor_label: None | str
    actor_user_id: None | str
    after: AuditEventReadAfterType0 | None
    before: AuditEventReadBeforeType0 | None
    entity_id: None | str
    entity_type: str
    id: str
    occurred_at: str
    request_id: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.audit_event_read_after_type_0 import AuditEventReadAfterType0
        from ..models.audit_event_read_before_type_0 import (
            AuditEventReadBeforeType0,
        )

        action = self.action

        action_class = self.action_class

        actor_display: None | str
        actor_display = self.actor_display

        actor_kind = self.actor_kind

        actor_label: None | str
        actor_label = self.actor_label

        actor_user_id: None | str
        actor_user_id = self.actor_user_id

        after: dict[str, Any] | None
        if isinstance(self.after, AuditEventReadAfterType0):
            after = self.after.to_dict()
        else:
            after = self.after

        before: dict[str, Any] | None
        if isinstance(self.before, AuditEventReadBeforeType0):
            before = self.before.to_dict()
        else:
            before = self.before

        entity_id: None | str
        entity_id = self.entity_id

        entity_type = self.entity_type

        id = self.id

        occurred_at = self.occurred_at

        request_id: None | str
        request_id = self.request_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "action_class": action_class,
                "actor_display": actor_display,
                "actor_kind": actor_kind,
                "actor_label": actor_label,
                "actor_user_id": actor_user_id,
                "after": after,
                "before": before,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "id": id,
                "occurred_at": occurred_at,
                "request_id": request_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.audit_event_read_after_type_0 import AuditEventReadAfterType0
        from ..models.audit_event_read_before_type_0 import (
            AuditEventReadBeforeType0,
        )

        d = dict(src_dict)
        action = d.pop("action")

        action_class = d.pop("action_class")

        def _parse_actor_display(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        actor_display = _parse_actor_display(d.pop("actor_display"))

        actor_kind = d.pop("actor_kind")

        def _parse_actor_label(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        actor_label = _parse_actor_label(d.pop("actor_label"))

        def _parse_actor_user_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        actor_user_id = _parse_actor_user_id(d.pop("actor_user_id"))

        def _parse_after(data: object) -> AuditEventReadAfterType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                after_type_0 = AuditEventReadAfterType0.from_dict(data)

                return after_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AuditEventReadAfterType0 | None, data)

        after = _parse_after(d.pop("after"))

        def _parse_before(data: object) -> AuditEventReadBeforeType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                before_type_0 = AuditEventReadBeforeType0.from_dict(data)

                return before_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AuditEventReadBeforeType0 | None, data)

        before = _parse_before(d.pop("before"))

        def _parse_entity_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        entity_id = _parse_entity_id(d.pop("entity_id"))

        entity_type = d.pop("entity_type")

        id = d.pop("id")

        occurred_at = d.pop("occurred_at")

        def _parse_request_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        request_id = _parse_request_id(d.pop("request_id"))

        audit_event_read = cls(
            action=action,
            action_class=action_class,
            actor_display=actor_display,
            actor_kind=actor_kind,
            actor_label=actor_label,
            actor_user_id=actor_user_id,
            after=after,
            before=before,
            entity_id=entity_id,
            entity_type=entity_type,
            id=id,
            occurred_at=occurred_at,
            request_id=request_id,
        )

        audit_event_read.additional_properties = d
        return audit_event_read

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
