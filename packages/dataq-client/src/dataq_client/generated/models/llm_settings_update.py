from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.llm_settings_update_provider import LlmSettingsUpdateProvider
from ..models.llm_settings_update_structured_output import LlmSettingsUpdateStructuredOutput
from ..types import UNSET, Unset

T = TypeVar("T", bound="LlmSettingsUpdate")


@_attrs_define
class LlmSettingsUpdate:
    """
    Attributes:
        model (str):
        provider (LlmSettingsUpdateProvider):
        api_key (None | str | Unset):
        base_url (None | str | Unset):
        connection_id (None | Unset | UUID):
        enabled (bool | Unset):  Default: True.
        structured_output (LlmSettingsUpdateStructuredOutput | Unset):  Default:
            LlmSettingsUpdateStructuredOutput.NATIVE.
    """

    model: str
    provider: LlmSettingsUpdateProvider
    api_key: None | str | Unset = UNSET
    base_url: None | str | Unset = UNSET
    connection_id: None | Unset | UUID = UNSET
    enabled: bool | Unset = True
    structured_output: LlmSettingsUpdateStructuredOutput | Unset = (
        LlmSettingsUpdateStructuredOutput.NATIVE
    )

    def to_dict(self) -> dict[str, Any]:
        model = self.model

        provider = self.provider.value

        api_key: None | str | Unset
        if isinstance(self.api_key, Unset):
            api_key = UNSET
        else:
            api_key = self.api_key

        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

        connection_id: None | str | Unset
        if isinstance(self.connection_id, Unset):
            connection_id = UNSET
        elif isinstance(self.connection_id, UUID):
            connection_id = str(self.connection_id)
        else:
            connection_id = self.connection_id

        enabled = self.enabled

        structured_output: str | Unset = UNSET
        if not isinstance(self.structured_output, Unset):
            structured_output = self.structured_output.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "model": model,
                "provider": provider,
            }
        )
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if connection_id is not UNSET:
            field_dict["connection_id"] = connection_id
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if structured_output is not UNSET:
            field_dict["structured_output"] = structured_output

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        model = d.pop("model")

        provider = LlmSettingsUpdateProvider(d.pop("provider"))

        def _parse_api_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_key = _parse_api_key(d.pop("api_key", UNSET))

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

        def _parse_connection_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                connection_id_type_0 = UUID(data)

                return connection_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        connection_id = _parse_connection_id(d.pop("connection_id", UNSET))

        enabled = d.pop("enabled", UNSET)

        _structured_output = d.pop("structured_output", UNSET)
        structured_output: LlmSettingsUpdateStructuredOutput | Unset
        if isinstance(_structured_output, Unset):
            structured_output = UNSET
        else:
            structured_output = LlmSettingsUpdateStructuredOutput(_structured_output)

        llm_settings_update = cls(
            model=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            connection_id=connection_id,
            enabled=enabled,
            structured_output=structured_output,
        )

        return llm_settings_update
