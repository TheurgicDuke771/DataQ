"""smtplib.SMTP stand-ins shared by the OTP delivery tests."""

from __future__ import annotations

import smtplib
from typing import Any, ClassVar


class CapturingSMTP:
    """Records (to, body) instead of speaking SMTP; usable as a context manager."""

    sent: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        pass

    def __enter__(self) -> CapturingSMTP:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message: Any) -> None:
        CapturingSMTP.sent.append((message["To"], message.get_content()))

    def quit(self) -> None:
        return None


class BrokenSMTP(CapturingSMTP):
    def send_message(self, message: Any) -> None:
        raise smtplib.SMTPServerDisconnected("relay went away")
