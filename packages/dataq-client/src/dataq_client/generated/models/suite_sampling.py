from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.suite_sampling_strategy import SuiteSamplingStrategy
from ..types import UNSET, Unset

T = TypeVar("T", bound="SuiteSampling")


@_attrs_define
class SuiteSampling:
    """Row-cap declaration on a run target (#595) — see `datasources.sampling`.

    Attributes:
        rows (int):
        strategy (SuiteSamplingStrategy):
        seed (int | None | Unset):
    """

    rows: int
    strategy: SuiteSamplingStrategy
    seed: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        rows = self.rows

        strategy = self.strategy.value

        seed: int | None | Unset
        if isinstance(self.seed, Unset):
            seed = UNSET
        else:
            seed = self.seed

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "rows": rows,
                "strategy": strategy,
            }
        )
        if seed is not UNSET:
            field_dict["seed"] = seed

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        rows = d.pop("rows")

        strategy = SuiteSamplingStrategy(d.pop("strategy"))

        def _parse_seed(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        seed = _parse_seed(d.pop("seed", UNSET))

        suite_sampling = cls(
            rows=rows,
            strategy=strategy,
            seed=seed,
        )

        return suite_sampling
