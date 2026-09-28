from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="LlmSettingsRead")


@_attrs_define
class LlmSettingsRead:
    """
    Attributes:
        configured (bool):
        base_url (None | str | Unset):
        connection_id (None | Unset | UUID):
        enabled (bool | Unset):  Default: False.
        has_credential (bool | Unset):  Default: False.
        model (None | str | Unset):
        provider (None | str | Unset):
        structured_output (None | str | Unset):
        updated_at (datetime.datetime | None | Unset):
    """

    configured: bool
    base_url: None | str | Unset = UNSET
    connection_id: None | Unset | UUID = UNSET
    enabled: bool | Unset = False
    has_credential: bool | Unset = False
    model: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    structured_output: None | str | Unset = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        configured = self.configured

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

        has_credential = self.has_credential

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        structured_output: None | str | Unset
        if isinstance(self.structured_output, Unset):
            structured_output = UNSET
        else:
            structured_output = self.structured_output

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "configured": configured,
            }
        )
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if connection_id is not UNSET:
            field_dict["connection_id"] = connection_id
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if has_credential is not UNSET:
            field_dict["has_credential"] = has_credential
        if model is not UNSET:
            field_dict["model"] = model
        if provider is not UNSET:
            field_dict["provider"] = provider
        if structured_output is not UNSET:
            field_dict["structured_output"] = structured_output
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        configured = d.pop("configured")

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

        has_credential = d.pop("has_credential", UNSET)

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        def _parse_structured_output(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        structured_output = _parse_structured_output(d.pop("structured_output", UNSET))

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = datetime.datetime.fromisoformat(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        llm_settings_read = cls(
            configured=configured,
            base_url=base_url,
            connection_id=connection_id,
            enabled=enabled,
            has_credential=has_credential,
            model=model,
            provider=provider,
            structured_output=structured_output,
            updated_at=updated_at,
        )

        llm_settings_read.additional_properties = d
        return llm_settings_read

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
