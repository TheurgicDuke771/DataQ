from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_validate_request_document_type_0 import SuiteValidateRequestDocumentType0


T = TypeVar("T", bound="SuiteValidateRequest")


@_attrs_define
class SuiteValidateRequest:
    """Like `SuiteImportRequest`, but `document` is taken as a plain object so that a
    document of the wrong shape is REPORTED in the response instead of refusing the
    request.

        Attributes:
            connection_id (UUID):
            document (None | SuiteValidateRequestDocumentType0 | Unset):
            document_yaml (None | str | Unset):
    """

    connection_id: UUID
    document: None | SuiteValidateRequestDocumentType0 | Unset = UNSET
    document_yaml: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_validate_request_document_type_0 import (
            SuiteValidateRequestDocumentType0,
        )

        connection_id = str(self.connection_id)

        document: dict[str, Any] | None | Unset
        if isinstance(self.document, Unset):
            document = UNSET
        elif isinstance(self.document, SuiteValidateRequestDocumentType0):
            document = self.document.to_dict()
        else:
            document = self.document

        document_yaml: None | str | Unset
        if isinstance(self.document_yaml, Unset):
            document_yaml = UNSET
        else:
            document_yaml = self.document_yaml

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
            }
        )
        if document is not UNSET:
            field_dict["document"] = document
        if document_yaml is not UNSET:
            field_dict["document_yaml"] = document_yaml

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_validate_request_document_type_0 import (
            SuiteValidateRequestDocumentType0,
        )

        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        def _parse_document(data: object) -> None | SuiteValidateRequestDocumentType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                document_type_0 = SuiteValidateRequestDocumentType0.from_dict(data)

                return document_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteValidateRequestDocumentType0 | Unset, data)

        document = _parse_document(d.pop("document", UNSET))

        def _parse_document_yaml(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_yaml = _parse_document_yaml(d.pop("document_yaml", UNSET))

        suite_validate_request = cls(
            connection_id=connection_id,
            document=document,
            document_yaml=document_yaml,
        )

        return suite_validate_request
