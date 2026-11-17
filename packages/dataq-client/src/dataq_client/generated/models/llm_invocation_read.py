from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.llm_invocation_read_response_type_0 import LlmInvocationReadResponseType0


T = TypeVar("T", bound="LlmInvocationRead")


@_attrs_define
class LlmInvocationRead:
    """
    Attributes:
        created_at (datetime.datetime):
        duration_ms (int | None):
        error (None | str):
        finished_at (datetime.datetime | None):
        id (UUID):
        input_tokens (int | None):
        kind (str):
        output_tokens (int | None):
        response (LlmInvocationReadResponseType0 | None):
        status (str):
        suite_id (None | UUID):
    """

    created_at: datetime.datetime
    duration_ms: int | None
    error: None | str
    finished_at: datetime.datetime | None
    id: UUID
    input_tokens: int | None
    kind: str
    output_tokens: int | None
    response: LlmInvocationReadResponseType0 | None
    status: str
    suite_id: None | UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.llm_invocation_read_response_type_0 import (
            LlmInvocationReadResponseType0,
        )

        created_at = self.created_at.isoformat()

        duration_ms: int | None
        duration_ms = self.duration_ms

        error: None | str
        error = self.error

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        id = str(self.id)

        input_tokens: int | None
        input_tokens = self.input_tokens

        kind = self.kind

        output_tokens: int | None
        output_tokens = self.output_tokens

        response: dict[str, Any] | None
        if isinstance(self.response, LlmInvocationReadResponseType0):
            response = self.response.to_dict()
        else:
            response = self.response

        status = self.status

        suite_id: None | str
        if isinstance(self.suite_id, UUID):
            suite_id = str(self.suite_id)
        else:
            suite_id = self.suite_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "duration_ms": duration_ms,
                "error": error,
                "finished_at": finished_at,
                "id": id,
                "input_tokens": input_tokens,
                "kind": kind,
                "output_tokens": output_tokens,
                "response": response,
                "status": status,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.llm_invocation_read_response_type_0 import (
            LlmInvocationReadResponseType0,
        )

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_duration_ms(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        duration_ms = _parse_duration_ms(d.pop("duration_ms"))

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        id = UUID(d.pop("id"))

        def _parse_input_tokens(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        input_tokens = _parse_input_tokens(d.pop("input_tokens"))

        kind = d.pop("kind")

        def _parse_output_tokens(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        output_tokens = _parse_output_tokens(d.pop("output_tokens"))

        def _parse_response(data: object) -> LlmInvocationReadResponseType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_type_0 = LlmInvocationReadResponseType0.from_dict(data)

                return response_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(LlmInvocationReadResponseType0 | None, data)

        response = _parse_response(d.pop("response"))

        status = d.pop("status")

        def _parse_suite_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                suite_id_type_0 = UUID(data)

                return suite_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        suite_id = _parse_suite_id(d.pop("suite_id"))

        llm_invocation_read = cls(
            created_at=created_at,
            duration_ms=duration_ms,
            error=error,
            finished_at=finished_at,
            id=id,
            input_tokens=input_tokens,
            kind=kind,
            output_tokens=output_tokens,
            response=response,
            status=status,
            suite_id=suite_id,
        )

        llm_invocation_read.additional_properties = d
        return llm_invocation_read

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
