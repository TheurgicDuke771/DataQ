from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_draft_test_config import ConnectionDraftTestConfig


T = TypeVar("T", bound="ConnectionDraftTest")


@_attrs_define
class ConnectionDraftTest:
    """The payload for `/connections/test` — everything `ConnectionCreate` needs to probe
    connectivity, minus `name` (a draft has no row and needs none).

        Attributes:
            type_ (str):
            catalog_secret (None | str | Unset): Second (catalog) credential to test; write-only
            config (ConnectionDraftTestConfig | Unset):
            env (None | str | Unset):
            secret (None | str | Unset): Credential to test; write-only
    """

    type_: str
    catalog_secret: None | str | Unset = UNSET
    config: ConnectionDraftTestConfig | Unset = UNSET
    env: None | str | Unset = UNSET
    secret: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_

        catalog_secret: None | str | Unset
        if isinstance(self.catalog_secret, Unset):
            catalog_secret = UNSET
        else:
            catalog_secret = self.catalog_secret

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        env: None | str | Unset
        if isinstance(self.env, Unset):
            env = UNSET
        else:
            env = self.env

        secret: None | str | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        else:
            secret = self.secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "type": type_,
            }
        )
        if catalog_secret is not UNSET:
            field_dict["catalog_secret"] = catalog_secret
        if config is not UNSET:
            field_dict["config"] = config
        if env is not UNSET:
            field_dict["env"] = env
        if secret is not UNSET:
            field_dict["secret"] = secret

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.connection_draft_test_config import ConnectionDraftTestConfig

        d = dict(src_dict)
        type_ = d.pop("type")

        def _parse_catalog_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        catalog_secret = _parse_catalog_secret(d.pop("catalog_secret", UNSET))

        _config = d.pop("config", UNSET)
        config: ConnectionDraftTestConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = ConnectionDraftTestConfig.from_dict(_config)

        def _parse_env(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        env = _parse_env(d.pop("env", UNSET))

        def _parse_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        connection_draft_test = cls(
            type_=type_,
            catalog_secret=catalog_secret,
            config=config,
            env=env,
            secret=secret,
        )

        return connection_draft_test
