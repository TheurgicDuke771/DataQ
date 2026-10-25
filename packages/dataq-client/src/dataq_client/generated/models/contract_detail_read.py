from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.compliance_read import ComplianceRead
    from ..models.contract_suite_read import ContractSuiteRead
    from ..models.unmapped_clause_read import UnmappedClauseRead


T = TypeVar("T", bound="ContractDetailRead")


@_attrs_define
class ContractDetailRead:
    """
    Attributes:
        api_version (str):
        compliance (ComplianceRead): Per clause, what its check found in its suite's latest run — over the suites the
            caller
            can see; `restricted_suite_count` on the contract counts the rest, which are not read.
        created_at (datetime.datetime):
        domain (None | str):
        id (UUID):
        name (str):
        odcs_id (str):
        restricted_suite_count (int):
        status (None | str):
        suites (list[ContractSuiteRead]):
        tenant (None | str):
        unmapped (list[UnmappedClauseRead]):
        version (str):
    """

    api_version: str
    compliance: ComplianceRead
    created_at: datetime.datetime
    domain: None | str
    id: UUID
    name: str
    odcs_id: str
    restricted_suite_count: int
    status: None | str
    suites: list[ContractSuiteRead]
    tenant: None | str
    unmapped: list[UnmappedClauseRead]
    version: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        api_version = self.api_version

        compliance = self.compliance.to_dict()

        created_at = self.created_at.isoformat()

        domain: None | str
        domain = self.domain

        id = str(self.id)

        name = self.name

        odcs_id = self.odcs_id

        restricted_suite_count = self.restricted_suite_count

        status: None | str
        status = self.status

        suites = []
        for suites_item_data in self.suites:
            suites_item = suites_item_data.to_dict()
            suites.append(suites_item)

        tenant: None | str
        tenant = self.tenant

        unmapped = []
        for unmapped_item_data in self.unmapped:
            unmapped_item = unmapped_item_data.to_dict()
            unmapped.append(unmapped_item)

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "api_version": api_version,
                "compliance": compliance,
                "created_at": created_at,
                "domain": domain,
                "id": id,
                "name": name,
                "odcs_id": odcs_id,
                "restricted_suite_count": restricted_suite_count,
                "status": status,
                "suites": suites,
                "tenant": tenant,
                "unmapped": unmapped,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.compliance_read import ComplianceRead
        from ..models.contract_suite_read import ContractSuiteRead
        from ..models.unmapped_clause_read import UnmappedClauseRead

        d = dict(src_dict)
        api_version = d.pop("api_version")

        compliance = ComplianceRead.from_dict(d.pop("compliance"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_domain(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        domain = _parse_domain(d.pop("domain"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        odcs_id = d.pop("odcs_id")

        restricted_suite_count = d.pop("restricted_suite_count")

        def _parse_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        status = _parse_status(d.pop("status"))

        suites = []
        _suites = d.pop("suites")
        for suites_item_data in _suites:
            suites_item = ContractSuiteRead.from_dict(suites_item_data)

            suites.append(suites_item)

        def _parse_tenant(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        tenant = _parse_tenant(d.pop("tenant"))

        unmapped = []
        _unmapped = d.pop("unmapped")
        for unmapped_item_data in _unmapped:
            unmapped_item = UnmappedClauseRead.from_dict(unmapped_item_data)

            unmapped.append(unmapped_item)

        version = d.pop("version")

        contract_detail_read = cls(
            api_version=api_version,
            compliance=compliance,
            created_at=created_at,
            domain=domain,
            id=id,
            name=name,
            odcs_id=odcs_id,
            restricted_suite_count=restricted_suite_count,
            status=status,
            suites=suites,
            tenant=tenant,
            unmapped=unmapped,
            version=version,
        )

        contract_detail_read.additional_properties = d
        return contract_detail_read

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
