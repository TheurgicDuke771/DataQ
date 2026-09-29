from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.gate_request_env import GateRequestEnv
from ..models.gate_request_fail_on import GateRequestFailOn
from ..models.gate_request_provider import GateRequestProvider
from ..types import UNSET, Unset

T = TypeVar("T", bound="GateRequest")


@_attrs_define
class GateRequest:
    """A pipeline stage asking whether it may continue (ADR 0046).

    Attributes:
        env (GateRequestEnv):
        pipeline_or_dag_id (str):
        provider (GateRequestProvider):
        provider_run_id (str):
        fail_on (GateRequestFailOn | Unset): The lowest check severity that fails the gate. Default:
            GateRequestFailOn.FAIL.
        trigger (bool | Unset): Start the bound suites' runs for this pipeline run (needs `edit`), or only report on
            runs that already exist for it (`view`). Default: True.
    """

    env: GateRequestEnv
    pipeline_or_dag_id: str
    provider: GateRequestProvider
    provider_run_id: str
    fail_on: GateRequestFailOn | Unset = GateRequestFailOn.FAIL
    trigger: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        env = self.env.value

        pipeline_or_dag_id = self.pipeline_or_dag_id

        provider = self.provider.value

        provider_run_id = self.provider_run_id

        fail_on: str | Unset = UNSET
        if not isinstance(self.fail_on, Unset):
            fail_on = self.fail_on.value

        trigger = self.trigger

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "env": env,
                "pipeline_or_dag_id": pipeline_or_dag_id,
                "provider": provider,
                "provider_run_id": provider_run_id,
            }
        )
        if fail_on is not UNSET:
            field_dict["fail_on"] = fail_on
        if trigger is not UNSET:
            field_dict["trigger"] = trigger

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        env = GateRequestEnv(d.pop("env"))

        pipeline_or_dag_id = d.pop("pipeline_or_dag_id")

        provider = GateRequestProvider(d.pop("provider"))

        provider_run_id = d.pop("provider_run_id")

        _fail_on = d.pop("fail_on", UNSET)
        fail_on: GateRequestFailOn | Unset
        if isinstance(_fail_on, Unset):
            fail_on = UNSET
        else:
            fail_on = GateRequestFailOn(_fail_on)

        trigger = d.pop("trigger", UNSET)

        gate_request = cls(
            env=env,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            provider_run_id=provider_run_id,
            fail_on=fail_on,
            trigger=trigger,
        )

        return gate_request
