from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.admin_credential_health_read import AdminCredentialHealthRead
    from ..models.beat_health_read import BeatHealthRead
    from ..models.poll_health_read import PollHealthRead
    from ..models.queue_depth_read import QueueDepthRead


T = TypeVar("T", bound="AdminHealthRead")


@_attrs_define
class AdminHealthRead:
    """Poll staleness + beat heartbeat + broker queue depth + datasource credential
    health, in one page (#1885/#1697).

    `queues` is `null` — never a fake `0` — when the broker could not be reached;
    `queues_error` then carries the classified, secret-free reason.

    `credentials` lists every DATASOURCE connection (orchestration providers are in
    `polling` instead), worst status first.

        Attributes:
            beat (BeatHealthRead): The beat→broker→worker heartbeat. `status="not_monitored"` means the heartbeat
                task has never recorded a tick — not the same as `alive`.
            credentials (list[AdminCredentialHealthRead]):
            generated_at (datetime.datetime):
            polling (list[PollHealthRead]):
            queues (list[QueueDepthRead] | None):
            queues_error (None | str):
    """

    beat: BeatHealthRead
    credentials: list[AdminCredentialHealthRead]
    generated_at: datetime.datetime
    polling: list[PollHealthRead]
    queues: list[QueueDepthRead] | None
    queues_error: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        beat = self.beat.to_dict()

        credentials = []
        for credentials_item_data in self.credentials:
            credentials_item = credentials_item_data.to_dict()
            credentials.append(credentials_item)

        generated_at = self.generated_at.isoformat()

        polling = []
        for polling_item_data in self.polling:
            polling_item = polling_item_data.to_dict()
            polling.append(polling_item)

        queues: list[dict[str, Any]] | None
        if isinstance(self.queues, list):
            queues = []
            for queues_type_0_item_data in self.queues:
                queues_type_0_item = queues_type_0_item_data.to_dict()
                queues.append(queues_type_0_item)

        else:
            queues = self.queues

        queues_error: None | str
        queues_error = self.queues_error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "beat": beat,
                "credentials": credentials,
                "generated_at": generated_at,
                "polling": polling,
                "queues": queues,
                "queues_error": queues_error,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.admin_credential_health_read import AdminCredentialHealthRead
        from ..models.beat_health_read import BeatHealthRead
        from ..models.poll_health_read import PollHealthRead
        from ..models.queue_depth_read import QueueDepthRead

        d = dict(src_dict)
        beat = BeatHealthRead.from_dict(d.pop("beat"))

        credentials = []
        _credentials = d.pop("credentials")
        for credentials_item_data in _credentials:
            credentials_item = AdminCredentialHealthRead.from_dict(credentials_item_data)

            credentials.append(credentials_item)

        generated_at = datetime.datetime.fromisoformat(d.pop("generated_at"))

        polling = []
        _polling = d.pop("polling")
        for polling_item_data in _polling:
            polling_item = PollHealthRead.from_dict(polling_item_data)

            polling.append(polling_item)

        def _parse_queues(data: object) -> list[QueueDepthRead] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                queues_type_0 = []
                _queues_type_0 = data
                for queues_type_0_item_data in _queues_type_0:
                    queues_type_0_item = QueueDepthRead.from_dict(queues_type_0_item_data)

                    queues_type_0.append(queues_type_0_item)

                return queues_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[QueueDepthRead] | None, data)

        queues = _parse_queues(d.pop("queues"))

        def _parse_queues_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        queues_error = _parse_queues_error(d.pop("queues_error"))

        admin_health_read = cls(
            beat=beat,
            credentials=credentials,
            generated_at=generated_at,
            polling=polling,
            queues=queues,
            queues_error=queues_error,
        )

        admin_health_read.additional_properties = d
        return admin_health_read

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
