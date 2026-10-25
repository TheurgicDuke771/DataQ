from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.contract_import_request_document_type_0 import ContractImportRequestDocumentType0


T = TypeVar("T", bound="ContractImportRequest")


@_attrs_define
class ContractImportRequest:
    """An ODCS v3 document as JSON (`document`) or YAML text (`document_yaml`) — exactly one.

    Attributes:
        connection_id (UUID): The connection the contract's suites run on (one suite per schema object).
        document (ContractImportRequestDocumentType0 | None | Unset):
        document_yaml (None | str | Unset):
        replace (bool | Unset): Replace an already-imported version (and reconcile its linked suites). Default: False.
        suite_ids (list[UUID] | None | Unset): For a NEW version: the suites linked to an earlier version of this
            contract that this version takes over and reconciles. Omit to create fresh suites.
    """

    connection_id: UUID
    document: ContractImportRequestDocumentType0 | None | Unset = UNSET
    document_yaml: None | str | Unset = UNSET
    replace: bool | Unset = False
    suite_ids: list[UUID] | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.contract_import_request_document_type_0 import (
            ContractImportRequestDocumentType0,
        )

        connection_id = str(self.connection_id)

        document: dict[str, Any] | None | Unset
        if isinstance(self.document, Unset):
            document = UNSET
        elif isinstance(self.document, ContractImportRequestDocumentType0):
            document = self.document.to_dict()
        else:
            document = self.document

        document_yaml: None | str | Unset
        if isinstance(self.document_yaml, Unset):
            document_yaml = UNSET
        else:
            document_yaml = self.document_yaml

        replace = self.replace

        suite_ids: list[str] | None | Unset
        if isinstance(self.suite_ids, Unset):
            suite_ids = UNSET
        elif isinstance(self.suite_ids, list):
            suite_ids = []
            for suite_ids_type_0_item_data in self.suite_ids:
                suite_ids_type_0_item = str(suite_ids_type_0_item_data)
                suite_ids.append(suite_ids_type_0_item)

        else:
            suite_ids = self.suite_ids

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
        if replace is not UNSET:
            field_dict["replace"] = replace
        if suite_ids is not UNSET:
            field_dict["suite_ids"] = suite_ids

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.contract_import_request_document_type_0 import (
            ContractImportRequestDocumentType0,
        )

        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        def _parse_document(data: object) -> ContractImportRequestDocumentType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                document_type_0 = ContractImportRequestDocumentType0.from_dict(data)

                return document_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ContractImportRequestDocumentType0 | None | Unset, data)

        document = _parse_document(d.pop("document", UNSET))

        def _parse_document_yaml(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_yaml = _parse_document_yaml(d.pop("document_yaml", UNSET))

        replace = d.pop("replace", UNSET)

        def _parse_suite_ids(data: object) -> list[UUID] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                suite_ids_type_0 = []
                _suite_ids_type_0 = data
                for suite_ids_type_0_item_data in _suite_ids_type_0:
                    suite_ids_type_0_item = UUID(suite_ids_type_0_item_data)

                    suite_ids_type_0.append(suite_ids_type_0_item)

                return suite_ids_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[UUID] | None | Unset, data)

        suite_ids = _parse_suite_ids(d.pop("suite_ids", UNSET))

        contract_import_request = cls(
            connection_id=connection_id,
            document=document,
            document_yaml=document_yaml,
            replace=replace,
            suite_ids=suite_ids,
        )

        return contract_import_request
