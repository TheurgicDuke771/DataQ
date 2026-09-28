from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_document import CheckDocument


T = TypeVar("T", bound="SuiteDocument")


@_attrs_define
class SuiteDocument:
    """Portable suite — connection-agnostic, no DB identity. The `GET /export`
    response shape; `SuiteDocumentIn` below is the identically-shaped import
    payload (#1505 — a response model stays lenient on `extra`, a request one
    doesn't).

        Attributes:
            name (str):
            checks (list[CheckDocument] | Unset):
            description (None | str | Unset):
            version (int | Unset):  Default: 1.
    """

    name: str
    checks: list[CheckDocument] | Unset = UNSET
    description: None | str | Unset = UNSET
    version: int | Unset = 1
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        checks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.checks, Unset):
            checks = []
            for checks_item_data in self.checks:
                checks_item = checks_item_data.to_dict()
                checks.append(checks_item)

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if checks is not UNSET:
            field_dict["checks"] = checks
        if description is not UNSET:
            field_dict["description"] = description
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_document import CheckDocument

        d = dict(src_dict)
        name = d.pop("name")

        _checks = d.pop("checks", UNSET)
        checks: list[CheckDocument] | Unset = UNSET
        if _checks is not UNSET:
            checks = []
            for checks_item_data in _checks:
                checks_item = CheckDocument.from_dict(checks_item_data)

                checks.append(checks_item)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        version = d.pop("version", UNSET)

        suite_document = cls(
            name=name,
            checks=checks,
            description=description,
            version=version,
        )

        suite_document.additional_properties = d
        return suite_document

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
