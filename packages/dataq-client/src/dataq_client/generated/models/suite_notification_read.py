from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SuiteNotificationRead")


@_attrs_define
class SuiteNotificationRead:
    """A suite's effective notification config. ``configured`` distinguishes a
    saved row from the defaults a suite falls back to. The Teams/Slack webhook URLs
    are secrets and never returned — only whether each is set (``has_*_webhook``).
    ``email_recipients`` is not a secret (addresses), so it's returned for prefill.

        Attributes:
            alert_on (str):
            configured (bool):
            email_recipients (None | str):
            enabled (bool):
            has_slack_webhook (bool):
            has_webhook (bool):
    """

    alert_on: str
    configured: bool
    email_recipients: None | str
    enabled: bool
    has_slack_webhook: bool
    has_webhook: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        alert_on = self.alert_on

        configured = self.configured

        email_recipients: None | str
        email_recipients = self.email_recipients

        enabled = self.enabled

        has_slack_webhook = self.has_slack_webhook

        has_webhook = self.has_webhook

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "alert_on": alert_on,
                "configured": configured,
                "email_recipients": email_recipients,
                "enabled": enabled,
                "has_slack_webhook": has_slack_webhook,
                "has_webhook": has_webhook,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        alert_on = d.pop("alert_on")

        configured = d.pop("configured")

        def _parse_email_recipients(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        email_recipients = _parse_email_recipients(d.pop("email_recipients"))

        enabled = d.pop("enabled")

        has_slack_webhook = d.pop("has_slack_webhook")

        has_webhook = d.pop("has_webhook")

        suite_notification_read = cls(
            alert_on=alert_on,
            configured=configured,
            email_recipients=email_recipients,
            enabled=enabled,
            has_slack_webhook=has_slack_webhook,
            has_webhook=has_webhook,
        )

        suite_notification_read.additional_properties = d
        return suite_notification_read

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
