from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.suite_notification_update_alert_on import SuiteNotificationUpdateAlertOn
from ..types import UNSET, Unset

T = TypeVar("T", bound="SuiteNotificationUpdate")


@_attrs_define
class SuiteNotificationUpdate:
    """`enabled`/`alert_on` plus channel links are the whole config (#1926). The
    three inline destinations below are accepted only as "" (clear a legacy
    value); any other value is refused for every caller.

        Attributes:
            alert_on (SuiteNotificationUpdateAlertOn | Unset):  Default: SuiteNotificationUpdateAlertOn.WARN.
            email_recipients (None | str | Unset):
            enabled (bool | Unset):  Default: True.
            slack_webhook (None | str | Unset):
            webhook (None | str | Unset):
    """

    alert_on: SuiteNotificationUpdateAlertOn | Unset = SuiteNotificationUpdateAlertOn.WARN
    email_recipients: None | str | Unset = UNSET
    enabled: bool | Unset = True
    slack_webhook: None | str | Unset = UNSET
    webhook: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        alert_on: str | Unset = UNSET
        if not isinstance(self.alert_on, Unset):
            alert_on = self.alert_on.value

        email_recipients: None | str | Unset
        if isinstance(self.email_recipients, Unset):
            email_recipients = UNSET
        else:
            email_recipients = self.email_recipients

        enabled = self.enabled

        slack_webhook: None | str | Unset
        if isinstance(self.slack_webhook, Unset):
            slack_webhook = UNSET
        else:
            slack_webhook = self.slack_webhook

        webhook: None | str | Unset
        if isinstance(self.webhook, Unset):
            webhook = UNSET
        else:
            webhook = self.webhook

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if alert_on is not UNSET:
            field_dict["alert_on"] = alert_on
        if email_recipients is not UNSET:
            field_dict["email_recipients"] = email_recipients
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if slack_webhook is not UNSET:
            field_dict["slack_webhook"] = slack_webhook
        if webhook is not UNSET:
            field_dict["webhook"] = webhook

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        _alert_on = d.pop("alert_on", UNSET)
        alert_on: SuiteNotificationUpdateAlertOn | Unset
        if isinstance(_alert_on, Unset):
            alert_on = UNSET
        else:
            alert_on = SuiteNotificationUpdateAlertOn(_alert_on)

        def _parse_email_recipients(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email_recipients = _parse_email_recipients(d.pop("email_recipients", UNSET))

        enabled = d.pop("enabled", UNSET)

        def _parse_slack_webhook(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slack_webhook = _parse_slack_webhook(d.pop("slack_webhook", UNSET))

        def _parse_webhook(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook = _parse_webhook(d.pop("webhook", UNSET))

        suite_notification_update = cls(
            alert_on=alert_on,
            email_recipients=email_recipients,
            enabled=enabled,
            slack_webhook=slack_webhook,
            webhook=webhook,
        )

        return suite_notification_update
