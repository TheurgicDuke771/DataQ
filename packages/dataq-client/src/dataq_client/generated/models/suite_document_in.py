from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_document_in import CheckDocumentIn


T = TypeVar("T", bound="SuiteDocumentIn")


@_attrs_define
class SuiteDocumentIn:
    """Request-side twin of `SuiteDocument`, accepted as the `import_suite`
    payload's nested document. `SuiteDocument` itself stays on `ApiModel`
    (`GET /export`'s response model) — `export_suite` always builds it from a
    closed field set, so nothing there needs `forbid`, and response models stay
    off it per `ApiRequestModel`'s own contract.

        Attributes:
            name (str):
            checks (list[CheckDocumentIn] | Unset):
            description (None | str | Unset):
            version (int | Unset):  Default: 1.
    """

    name: str
    checks: list[CheckDocumentIn] | Unset = UNSET
    description: None | str | Unset = UNSET
    version: int | Unset = 1

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
        from ..models.check_document_in import CheckDocumentIn

        d = dict(src_dict)
        name = d.pop("name")

        _checks = d.pop("checks", UNSET)
        checks: list[CheckDocumentIn] | Unset = UNSET
        if _checks is not UNSET:
            checks = []
            for checks_item_data in _checks:
                checks_item = CheckDocumentIn.from_dict(checks_item_data)

                checks.append(checks_item)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        version = d.pop("version", UNSET)

        suite_document_in = cls(
            name=name,
            checks=checks,
            description=description,
            version=version,
        )

        return suite_document_in
