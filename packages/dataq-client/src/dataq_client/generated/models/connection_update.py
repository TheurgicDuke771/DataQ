from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_update_config_type_0 import ConnectionUpdateConfigType0


T = TypeVar("T", bound="ConnectionUpdate")


@_attrs_define
class ConnectionUpdate:
    """
    Attributes:
        catalog_secret (None | str | Unset): Rotate the second (catalog) credential; write-only
        config (ConnectionUpdateConfigType0 | None | Unset):
        name (None | str | Unset):
        secret (None | str | Unset): Rotate the credential; write-only
    """

    catalog_secret: None | str | Unset = UNSET
    config: ConnectionUpdateConfigType0 | None | Unset = UNSET
    name: None | str | Unset = UNSET
    secret: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.connection_update_config_type_0 import (
            ConnectionUpdateConfigType0,
        )

        catalog_secret: None | str | Unset
        if isinstance(self.catalog_secret, Unset):
            catalog_secret = UNSET
        else:
            catalog_secret = self.catalog_secret

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, ConnectionUpdateConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        secret: None | str | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        else:
            secret = self.secret

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if catalog_secret is not UNSET:
            field_dict["catalog_secret"] = catalog_secret
        if config is not UNSET:
            field_dict["config"] = config
        if name is not UNSET:
            field_dict["name"] = name
        if secret is not UNSET:
            field_dict["secret"] = secret

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.connection_update_config_type_0 import (
            ConnectionUpdateConfigType0,
        )

        d = dict(src_dict)

        def _parse_catalog_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog_secret = _parse_catalog_secret(d.pop("catalog_secret", UNSET))

        def _parse_config(data: object) -> ConnectionUpdateConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = ConnectionUpdateConfigType0.from_dict(data)

                return config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectionUpdateConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        connection_update = cls(
            catalog_secret=catalog_secret,
            config=config,
            name=name,
            secret=secret,
        )

        return connection_update
