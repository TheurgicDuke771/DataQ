"""`scripts/clear_misprobed_dmf_capability` — dry run by default (#2112)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.scripts import clear_misprobed_dmf_capability as script


class _Session:
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("argv", "apply"), [([], False), (["--apply"], True)])
def test_script_is_a_dry_run_unless_told_to_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, argv: list[str], apply: bool
) -> None:
    session = _Session()
    seen: dict[str, Any] = {}
    conn_id = uuid.uuid4()

    def fake_clear(s: Any, *, apply: bool) -> list[uuid.UUID]:
        seen["session"], seen["apply"] = s, apply
        return [conn_id]

    monkeypatch.setattr(script, "get_session", lambda: session)
    monkeypatch.setattr(script, "clear_misprobed_dmf_capabilities", fake_clear)

    assert script.main(argv) == 0
    assert seen == {"session": session, "apply": apply}
    assert session.closed
    out = capsys.readouterr().out
    assert ("cleared" if apply else "would clear") in out
    assert str(conn_id) in out
