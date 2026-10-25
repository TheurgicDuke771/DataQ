from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.clause_state_read import ClauseStateRead
    from ..models.compliance_read_counts import ComplianceReadCounts
    from ..models.suite_compliance_read import SuiteComplianceRead


T = TypeVar("T", bound="ComplianceRead")


@_attrs_define
class ComplianceRead:
    """Per clause, what its check found in its suite's latest run — over the suites the caller
    can see; `restricted_suite_count` on the contract counts the rest, which are not read.

        Attributes:
            clauses (list[ClauseStateRead]):
            counts (ComplianceReadCounts):
            not_enforced (int):
            suites (list[SuiteComplianceRead]):
    """

    clauses: list[ClauseStateRead]
    counts: ComplianceReadCounts
    not_enforced: int
    suites: list[SuiteComplianceRead]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        clauses = []
        for clauses_item_data in self.clauses:
            clauses_item = clauses_item_data.to_dict()
            clauses.append(clauses_item)

        counts = self.counts.to_dict()

        not_enforced = self.not_enforced

        suites = []
        for suites_item_data in self.suites:
            suites_item = suites_item_data.to_dict()
            suites.append(suites_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "clauses": clauses,
                "counts": counts,
                "not_enforced": not_enforced,
                "suites": suites,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.clause_state_read import ClauseStateRead
        from ..models.compliance_read_counts import ComplianceReadCounts
        from ..models.suite_compliance_read import SuiteComplianceRead

        d = dict(src_dict)
        clauses = []
        _clauses = d.pop("clauses")
        for clauses_item_data in _clauses:
            clauses_item = ClauseStateRead.from_dict(clauses_item_data)

            clauses.append(clauses_item)

        counts = ComplianceReadCounts.from_dict(d.pop("counts"))

        not_enforced = d.pop("not_enforced")

        suites = []
        _suites = d.pop("suites")
        for suites_item_data in _suites:
            suites_item = SuiteComplianceRead.from_dict(suites_item_data)

            suites.append(suites_item)

        compliance_read = cls(
            clauses=clauses,
            counts=counts,
            not_enforced=not_enforced,
            suites=suites,
        )

        compliance_read.additional_properties = d
        return compliance_read

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
