from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.suite_document_in import SuiteDocumentIn


T = TypeVar("T", bound="SuiteImportRequest")


@_attrs_define
class SuiteImportRequest:
    """
    Attributes:
        connection_id (UUID):
        document (SuiteDocumentIn): Request-side twin of `SuiteDocument`, accepted as the `import_suite`
            payload's nested document. `SuiteDocument` itself stays on `ApiModel`
            (`GET /export`'s response model) — `export_suite` always builds it from a
            closed field set, so nothing there needs `forbid`, and response models stay
            off it per `ApiRequestModel`'s own contract.
    """

    connection_id: UUID
    document: SuiteDocumentIn

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        document = self.document.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
                "document": document,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_document_in import SuiteDocumentIn

        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        document = SuiteDocumentIn.from_dict(d.pop("document"))

        suite_import_request = cls(
            connection_id=connection_id,
            document=document,
        )

        return suite_import_request
