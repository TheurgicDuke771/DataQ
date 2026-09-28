from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_dry_run_request_config import CheckDryRunRequestConfig


T = TypeVar("T", bound="CheckDryRunRequest")


@_attrs_define
class CheckDryRunRequest:
    """
    Attributes:
        expectation_type (str):
        config (CheckDryRunRequestConfig | Unset):
        critical_threshold (float | None | str | Unset):
        engine (str | Unset):  Default: 'gx'.
        fail_threshold (float | None | str | Unset):
        kind (str | Unset):  Default: 'expectation'.
        warn_threshold (float | None | str | Unset):
    """

    expectation_type: str
    config: CheckDryRunRequestConfig | Unset = UNSET
    critical_threshold: float | None | str | Unset = UNSET
    engine: str | Unset = "gx"
    fail_threshold: float | None | str | Unset = UNSET
    kind: str | Unset = "expectation"
    warn_threshold: float | None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        expectation_type = self.expectation_type

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        critical_threshold: float | None | str | Unset
        if isinstance(self.critical_threshold, Unset):
            critical_threshold = UNSET
        else:
            critical_threshold = self.critical_threshold

        engine = self.engine

        fail_threshold: float | None | str | Unset
        if isinstance(self.fail_threshold, Unset):
            fail_threshold = UNSET
        else:
            fail_threshold = self.fail_threshold

        kind = self.kind

        warn_threshold: float | None | str | Unset
        if isinstance(self.warn_threshold, Unset):
            warn_threshold = UNSET
        else:
            warn_threshold = self.warn_threshold

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expectation_type": expectation_type,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if critical_threshold is not UNSET:
            field_dict["critical_threshold"] = critical_threshold
        if engine is not UNSET:
            field_dict["engine"] = engine
        if fail_threshold is not UNSET:
            field_dict["fail_threshold"] = fail_threshold
        if kind is not UNSET:
            field_dict["kind"] = kind
        if warn_threshold is not UNSET:
            field_dict["warn_threshold"] = warn_threshold

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_dry_run_request_config import CheckDryRunRequestConfig

        d = dict(src_dict)
        expectation_type = d.pop("expectation_type")

        _config = d.pop("config", UNSET)
        config: CheckDryRunRequestConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = CheckDryRunRequestConfig.from_dict(_config)

        def _parse_critical_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        critical_threshold = _parse_critical_threshold(d.pop("critical_threshold", UNSET))

        engine = d.pop("engine", UNSET)

        def _parse_fail_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold", UNSET))

        kind = d.pop("kind", UNSET)

        def _parse_warn_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold", UNSET))

        check_dry_run_request = cls(
            expectation_type=expectation_type,
            config=config,
            critical_threshold=critical_threshold,
            engine=engine,
            fail_threshold=fail_threshold,
            kind=kind,
            warn_threshold=warn_threshold,
        )

        return check_dry_run_request
