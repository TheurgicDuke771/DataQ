from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.audit_chain_status_anchor_mode import AuditChainStatusAnchorMode
from ..models.audit_chain_status_status import AuditChainStatusStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.anchor_verification_read import AnchorVerificationRead
    from ..models.audit_chain_status_first_break_type_0 import AuditChainStatusFirstBreakType0


T = TypeVar("T", bound="AuditChainStatus")


@_attrs_define
class AuditChainStatus:
    """The verification tooling ADR 0041 §9 / #1460 requires — not just a
    boolean, so an admin can see WHAT is or isn't covered.

        Attributes:
            anchor_mode (AuditChainStatusAnchorMode):
            chain_head_hash (None | str):
            first_break (AuditChainStatusFirstBreakType0 | None):
            status (AuditChainStatusStatus):
            unverifiable_legacy_count (int):
            verified_count (int):
            anchors (AnchorVerificationRead | None | Unset):
    """

    anchor_mode: AuditChainStatusAnchorMode
    chain_head_hash: None | str
    first_break: AuditChainStatusFirstBreakType0 | None
    status: AuditChainStatusStatus
    unverifiable_legacy_count: int
    verified_count: int
    anchors: AnchorVerificationRead | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.anchor_verification_read import AnchorVerificationRead
        from ..models.audit_chain_status_first_break_type_0 import (
            AuditChainStatusFirstBreakType0,
        )

        anchor_mode = self.anchor_mode.value

        chain_head_hash: None | str
        chain_head_hash = self.chain_head_hash

        first_break: dict[str, Any] | None
        if isinstance(self.first_break, AuditChainStatusFirstBreakType0):
            first_break = self.first_break.to_dict()
        else:
            first_break = self.first_break

        status = self.status.value

        unverifiable_legacy_count = self.unverifiable_legacy_count

        verified_count = self.verified_count

        anchors: dict[str, Any] | None | Unset
        if isinstance(self.anchors, Unset):
            anchors = UNSET
        elif isinstance(self.anchors, AnchorVerificationRead):
            anchors = self.anchors.to_dict()
        else:
            anchors = self.anchors

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "anchor_mode": anchor_mode,
                "chain_head_hash": chain_head_hash,
                "first_break": first_break,
                "status": status,
                "unverifiable_legacy_count": unverifiable_legacy_count,
                "verified_count": verified_count,
            }
        )
        if anchors is not UNSET:
            field_dict["anchors"] = anchors

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.anchor_verification_read import AnchorVerificationRead
        from ..models.audit_chain_status_first_break_type_0 import (
            AuditChainStatusFirstBreakType0,
        )

        d = dict(src_dict)
        anchor_mode = AuditChainStatusAnchorMode(d.pop("anchor_mode"))

        def _parse_chain_head_hash(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        chain_head_hash = _parse_chain_head_hash(d.pop("chain_head_hash"))

        def _parse_first_break(data: object) -> AuditChainStatusFirstBreakType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                first_break_type_0 = AuditChainStatusFirstBreakType0.from_dict(data)

                return first_break_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AuditChainStatusFirstBreakType0 | None, data)

        first_break = _parse_first_break(d.pop("first_break"))

        status = AuditChainStatusStatus(d.pop("status"))

        unverifiable_legacy_count = d.pop("unverifiable_legacy_count")

        verified_count = d.pop("verified_count")

        def _parse_anchors(data: object) -> AnchorVerificationRead | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                anchors_type_0 = AnchorVerificationRead.from_dict(data)

                return anchors_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AnchorVerificationRead | None | Unset, data)

        anchors = _parse_anchors(d.pop("anchors", UNSET))

        audit_chain_status = cls(
            anchor_mode=anchor_mode,
            chain_head_hash=chain_head_hash,
            first_break=first_break,
            status=status,
            unverifiable_legacy_count=unverifiable_legacy_count,
            verified_count=verified_count,
            anchors=anchors,
        )

        audit_chain_status.additional_properties = d
        return audit_chain_status

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
