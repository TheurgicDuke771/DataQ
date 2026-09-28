from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_create_config import ConnectionCreateConfig


T = TypeVar("T", bound="ConnectionCreate")


@_attrs_define
class ConnectionCreate:
    """
    Attributes:
        env (str):
        name (str):
        type_ (str):
        catalog_secret (None | str | Unset): Second credential a connection type may need (currently the Iceberg SQL-
            catalog DB password, #1181); write-only, never returned
        config (ConnectionCreateConfig | Unset):
        secret (None | str | Unset): Credential; write-only, never returned
    """

    env: str
    name: str
    type_: str
    catalog_secret: None | str | Unset = UNSET
    config: ConnectionCreateConfig | Unset = UNSET
    secret: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        env = self.env

        name = self.name

        type_ = self.type_

        catalog_secret: None | str | Unset
        if isinstance(self.catalog_secret, Unset):
            catalog_secret = UNSET
        else:
            catalog_secret = self.catalog_secret

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        secret: None | str | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        else:
            secret = self.secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "env": env,
                "name": name,
                "type": type_,
            }
        )
        if catalog_secret is not UNSET:
            field_dict["catalog_secret"] = catalog_secret
        if config is not UNSET:
            field_dict["config"] = config
        if secret is not UNSET:
            field_dict["secret"] = secret

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.connection_create_config import ConnectionCreateConfig

        d = dict(src_dict)
        env = d.pop("env")

        name = d.pop("name")

        type_ = d.pop("type")

        def _parse_catalog_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog_secret = _parse_catalog_secret(d.pop("catalog_secret", UNSET))

        _config = d.pop("config", UNSET)
        config: ConnectionCreateConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = ConnectionCreateConfig.from_dict(_config)

        def _parse_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        connection_create = cls(
            env=env,
            name=name,
            type_=type_,
            catalog_secret=catalog_secret,
            config=config,
            secret=secret,
        )

        return connection_create
