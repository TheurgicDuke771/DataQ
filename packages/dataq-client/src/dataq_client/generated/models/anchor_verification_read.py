from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.anchor_verification_read_status import AnchorVerificationReadStatus

if TYPE_CHECKING:
    from ..models.anchor_finding import AnchorFinding


T = TypeVar("T", bound="AnchorVerificationRead")


@_attrs_define
class AnchorVerificationRead:
    """
    Attributes:
        checked_count (int):
        error (None | str):
        findings (list[AnchorFinding]):
        malformed_count (int):
        missing_count (int):
        present_count (int):
        purged_count (int):
        status (AnchorVerificationReadStatus):
        truncated (bool):
    """

    checked_count: int
    error: None | str
    findings: list[AnchorFinding]
    malformed_count: int
    missing_count: int
    present_count: int
    purged_count: int
    status: AnchorVerificationReadStatus
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checked_count = self.checked_count

        error: None | str
        error = self.error

        findings = []
        for findings_item_data in self.findings:
            findings_item = findings_item_data.to_dict()
            findings.append(findings_item)

        malformed_count = self.malformed_count

        missing_count = self.missing_count

        present_count = self.present_count

        purged_count = self.purged_count

        status = self.status.value

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checked_count": checked_count,
                "error": error,
                "findings": findings,
                "malformed_count": malformed_count,
                "missing_count": missing_count,
                "present_count": present_count,
                "purged_count": purged_count,
                "status": status,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.anchor_finding import AnchorFinding

        d = dict(src_dict)
        checked_count = d.pop("checked_count")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        findings = []
        _findings = d.pop("findings")
        for findings_item_data in _findings:
            findings_item = AnchorFinding.from_dict(findings_item_data)

            findings.append(findings_item)

        malformed_count = d.pop("malformed_count")

        missing_count = d.pop("missing_count")

        present_count = d.pop("present_count")

        purged_count = d.pop("purged_count")

        status = AnchorVerificationReadStatus(d.pop("status"))

        truncated = d.pop("truncated")

        anchor_verification_read = cls(
            checked_count=checked_count,
            error=error,
            findings=findings,
            malformed_count=malformed_count,
            missing_count=missing_count,
            present_count=present_count,
            purged_count=purged_count,
            status=status,
            truncated=truncated,
        )

        anchor_verification_read.additional_properties = d
        return anchor_verification_read

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
