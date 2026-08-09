"""The `publish_health` seam method (#837) — composite semantics (#1101).

Mirrors `test_poll_staleness_publish.py`'s `TestCompositeStalenessContract` exactly:
`worker.tasks.publish_connection_health` records the #843 delivered-first flag
based on whether the composite's `publish_health` call raised, so "every channel
failed or every channel quietly skipped as unconfigured" must raise (else a total
delivery failure — including the shipped default of zero configured channels —
would be recorded as delivered and the edge would never retry), while one
surviving channel must swallow the rest (partial delivery counts, exactly like
the run path's channel isolation).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    HEALTH_FAILING,
    AlertUndeliverableError,
    ConnectionHealthReport,
    was_already_logged,
)
from backend.app.alerting.composite import CompositePublisher

_SESSION = cast(Session, None)  # the composite forwards it opaquely; no DB is touched


def _report() -> ConnectionHealthReport:
    import uuid

    return ConnectionHealthReport(
        connection_id=uuid.uuid4(),
        connection_name="dbt-prod",
        connection_type="dbt",
        state=HEALTH_FAILING,
        consecutive_failures=3,
        reason="auth_failed",
        last_polled_at=None,
    )


class _Channel:
    def __init__(self, fail: bool = False, unconfigured: bool = False) -> None:
        self.fail = fail
        self.unconfigured = unconfigured
        self.health_reports: list[ConnectionHealthReport] = []

    def publish(self, session: Any, report: Any) -> None: ...

    def publish_health(self, session: Any, report: ConnectionHealthReport) -> bool:
        if self.fail:
            raise RuntimeError("channel down")
        if self.unconfigured:
            return False  # the real channels' quiet-skip: nothing left the process
        self.health_reports.append(report)
        return True

    def publish_poll_staleness(self, session: Any, report: Any) -> bool:
        return True


class TestCompositeHealthContract:
    def test_fans_out_to_every_channel(self) -> None:
        channels = [_Channel(), _Channel()]
        CompositePublisher(channels).publish_health(_SESSION, _report())
        assert all(len(c.health_reports) == 1 for c in channels)

    def test_one_broken_channel_does_not_stop_the_others_or_raise(self) -> None:
        broken, alive = _Channel(fail=True), _Channel()
        CompositePublisher([broken, alive]).publish_health(_SESSION, _report())
        assert len(alive.health_reports) == 1

    def test_every_channel_failing_raises_so_the_flag_is_never_falsely_recorded(self) -> None:
        """The delivered-first hinge: if this silently returned, the caller would
        stamp `health_alerted_at` for an alert nobody received — and never retry."""
        with pytest.raises(RuntimeError):
            CompositePublisher([_Channel(fail=True), _Channel(fail=True)]).publish_health(
                _SESSION, _report()
            )

    def test_the_reraised_error_is_marked_already_logged(self) -> None:
        """#1226: the loop above already logs a full traceback for EVERY failing
        channel (including the last one, whose exception is what gets re-raised
        here) — so a caller's own `except Exception: log.exception(...)` would log
        that same traceback again unless it can tell it was already reported."""
        with pytest.raises(RuntimeError) as exc_info:
            CompositePublisher([_Channel(fail=True), _Channel(fail=True)]).publish_health(
                _SESSION, _report()
            )
        assert was_already_logged(exc_info.value)

    def test_a_freshly_raised_error_is_not_marked(self) -> None:
        """Sanity check on the marker itself, so the positive test above isn't
        trivially true — an ordinary exception nobody has marked must read as NOT
        already logged."""
        assert not was_already_logged(RuntimeError("never touched the composite"))

    def test_all_channels_unconfigured_raises_undeliverable(self) -> None:
        """The fresh-install trap (#1101): every real channel quietly no-ops when
        unconfigured, so counting a returned call as delivered would stamp the flag
        with ZERO notifications sent — and when an operator later wires up Slack,
        the still-outstanding incident would never fire."""
        with pytest.raises(AlertUndeliverableError):
            CompositePublisher(
                [_Channel(unconfigured=True), _Channel(unconfigured=True)]
            ).publish_health(_SESSION, _report())

    def test_one_configured_channel_is_enough(self) -> None:
        skipped, alive = _Channel(unconfigured=True), _Channel()
        CompositePublisher([skipped, alive]).publish_health(_SESSION, _report())
        assert len(alive.health_reports) == 1

    def test_unconfigured_real_channels_report_not_delivered(self) -> None:
        """The real channels (not just the fake) must return False on their quiet
        skips — Teams/Slack resolve no webhook, email has no transport config."""
        from backend.app.alerting.email import EmailPublisher
        from backend.app.alerting.slack import SlackPublisher
        from backend.app.alerting.teams import TeamsPublisher

        class _EmptyStore:
            def get(self, name: str) -> str:
                from backend.app.core.secrets import SecretNotFoundError

                raise SecretNotFoundError(name)

        store = _EmptyStore()
        teams = TeamsPublisher(
            secret_store=store,  # type: ignore[arg-type]
            workspace_secret_name=None,
        )
        slack = SlackPublisher(
            secret_store=store,  # type: ignore[arg-type]
            webhook_secret_name=None,
            allowed_hosts=("hooks.slack.com",),
        )
        email = EmailPublisher(
            secret_store=store,  # type: ignore[arg-type]
            smtp_host="localhost",
            smtp_port=25,
            username=None,
            password_secret_name=None,
            sender=None,
            recipients=(),
        )
        assert teams.publish_health(_SESSION, _report()) is False
        assert slack.publish_health(_SESSION, _report()) is False
        assert email.publish_health(_SESSION, _report()) is False

    def test_a_workspace_with_zero_real_channels_configured_raises_undeliverable(self) -> None:
        """End-to-end at the composite level with the REAL channel implementations
        (not the fake `_Channel`) — the exact shape of the shipped default: a fresh
        install with no Teams/Slack/email secret configured anywhere."""
        from backend.app.alerting.email import EmailPublisher
        from backend.app.alerting.slack import SlackPublisher
        from backend.app.alerting.teams import TeamsPublisher

        class _EmptyStore:
            def get(self, name: str) -> str:
                from backend.app.core.secrets import SecretNotFoundError

                raise SecretNotFoundError(name)

        store = _EmptyStore()
        composite = CompositePublisher(
            [
                TeamsPublisher(secret_store=store, workspace_secret_name=None),  # type: ignore[arg-type]
                SlackPublisher(
                    secret_store=store,  # type: ignore[arg-type]
                    webhook_secret_name=None,
                    allowed_hosts=("hooks.slack.com",),
                ),
                EmailPublisher(
                    secret_store=store,  # type: ignore[arg-type]
                    smtp_host="localhost",
                    smtp_port=25,
                    username=None,
                    password_secret_name=None,
                    sender=None,
                    recipients=(),
                ),
            ]
        )
        with pytest.raises(AlertUndeliverableError):
            composite.publish_health(_SESSION, _report())

    def test_noop_publisher_counts_as_delivered(self) -> None:
        """`NoopPublisher` is the explicit test double for "a channel that IS
        configured and sends" — mirroring `publish_poll_staleness`'s convention —
        so a composite built from just it must never raise undeliverable."""
        from backend.app.alerting.noop import NoopPublisher

        # Must not raise.
        assert NoopPublisher().publish_health(_SESSION, _report()) is True
        CompositePublisher([NoopPublisher()]).publish_health(_SESSION, _report())
