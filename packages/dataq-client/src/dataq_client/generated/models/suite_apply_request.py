from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_document_in import SuiteDocumentIn


T = TypeVar("T", bound="SuiteApplyRequest")


@_attrs_define
class SuiteApplyRequest:
    """A suite document to apply onto THIS suite — JSON (`document`) or YAML text
    (`document_yaml`), exactly one.

        Attributes:
            document (None | SuiteDocumentIn | Unset):
            document_yaml (None | str | Unset):
            dry_run (bool | Unset): Report what would change and change nothing. This is the drift check. Default: False.
            prune (bool | Unset): Also delete the suite's checks that the document does not name. Default: False.
    """

    document: None | SuiteDocumentIn | Unset = UNSET
    document_yaml: None | str | Unset = UNSET
    dry_run: bool | Unset = False
    prune: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_document_in import SuiteDocumentIn

        document: dict[str, Any] | None | Unset
        if isinstance(self.document, Unset):
            document = UNSET
        elif isinstance(self.document, SuiteDocumentIn):
            document = self.document.to_dict()
        else:
            document = self.document

        document_yaml: None | str | Unset
        if isinstance(self.document_yaml, Unset):
            document_yaml = UNSET
        else:
            document_yaml = self.document_yaml

        dry_run = self.dry_run

        prune = self.prune

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if document is not UNSET:
            field_dict["document"] = document
        if document_yaml is not UNSET:
            field_dict["document_yaml"] = document_yaml
        if dry_run is not UNSET:
            field_dict["dry_run"] = dry_run
        if prune is not UNSET:
            field_dict["prune"] = prune

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_document_in import SuiteDocumentIn

        d = dict(src_dict)

        def _parse_document(data: object) -> None | SuiteDocumentIn | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                document_type_0 = SuiteDocumentIn.from_dict(data)

                return document_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteDocumentIn | Unset, data)

        document = _parse_document(d.pop("document", UNSET))

        def _parse_document_yaml(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_yaml = _parse_document_yaml(d.pop("document_yaml", UNSET))

        dry_run = d.pop("dry_run", UNSET)

        prune = d.pop("prune", UNSET)

        suite_apply_request = cls(
            document=document,
            document_yaml=document_yaml,
            dry_run=dry_run,
            prune=prune,
        )

        return suite_apply_request
