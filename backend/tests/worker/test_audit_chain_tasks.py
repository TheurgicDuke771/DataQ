"""The daily `verify_audit_chain` task reports anchor read-back, not only the chain — #2310."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from backend.app.services import audit_anchor_service, audit_chain
from backend.app.services.audit_anchor_service import AnchorCheck, AnchorVerification
from backend.app.services.audit_chain import ChainVerification
from backend.app.worker import tasks


class _Session:
    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "get_session", _Session)
    monkeypatch.setattr(
        audit_chain,
        "verify_chain",
        lambda _s: ChainVerification(
            verified_count=3, unverifiable_legacy_count=0, chain_head_hash="h", first_break=None
        ),
    )


def _anchors(monkeypatch: pytest.MonkeyPatch, result: AnchorVerification | None) -> None:
    monkeypatch.setattr(audit_anchor_service, "verify_anchors", lambda _s: result)


def test_a_clean_chain_with_no_readable_anchor_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    _anchors(monkeypatch, None)
    assert tasks.verify_audit_chain() is True


def test_an_anchor_mismatch_is_logged_and_fails_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restore leaves the chain internally consistent; only the anchors see it, so
    the unattended daily check must not report the chain as clean."""
    _anchors(
        monkeypatch,
        AnchorVerification(
            status="mismatch",
            checked_count=2,
            present_count=1,
            missing_count=1,
            findings=[AnchorCheck("audit-anchors/x.json", "d", "ff", None, "missing")],
        ),
    )
    with capture_logs() as logs:
        assert tasks.verify_audit_chain() is False
    [entry] = [e for e in logs if e.get("event") == "audit_anchor_mismatch"]
    assert entry["log_level"] == "error"
    assert entry["missing_count"] == 1
    assert entry["findings"] == ["missing:audit-anchors/x.json"]


def test_an_unreadable_anchor_store_is_logged_and_fails_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _anchors(monkeypatch, AnchorVerification(status="unavailable", error="ConnectError: x"))
    with capture_logs() as logs:
        assert tasks.verify_audit_chain() is False
    assert any(e.get("event") == "audit_anchor_unverified" for e in logs)


@pytest.mark.parametrize("status", ["ok", "empty"])
def test_clean_or_empty_anchors_do_not_fail_the_task(
    monkeypatch: pytest.MonkeyPatch, status: Any
) -> None:
    _anchors(monkeypatch, AnchorVerification(status=status))
    assert tasks.verify_audit_chain() is True
