"""Nothing in the app may mutate or delete an existing audit row — ADR 0041 §2.7 (#1318)."""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

#: The single module allowed to delete audit rows: the retention sweep, which ADR 0041 §2.7 requires
#: and which re-grants the privilege around its own statement.
_DELETE_ALLOWED = {"services/audit_read_service.py"}

#: No module at all may UPDATE an audit row.
_UPDATE_ALLOWED: set[str] = set()


def _python_sources() -> list[Path]:
    return sorted(p for p in _APP.rglob("*.py") if "__pycache__" not in p.parts)


def _calls_on_audit_event(tree: ast.AST, func_names: set[str]) -> bool:
    """Whether the module calls one of `func_names` with `AuditEvent` as an
    argument — `delete(AuditEvent)`, `update(AuditEvent)`, the SQLAlchemy Core
    forms a bulk mutation actually uses.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        if name not in func_names:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id == "AuditEvent":
                return True
            if isinstance(arg, ast.Attribute) and arg.attr.startswith("AuditEvent"):
                return True
    return False


def test_only_the_retention_sweep_deletes_audit_rows() -> None:
    """A `delete(AuditEvent)` anywhere else is the failure this table exists to
    prevent: the record of an act disappearing more quietly than the act did.
    """
    offenders = []
    for path in _python_sources():
        rel = str(path.relative_to(_APP))
        if rel in _DELETE_ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        if _calls_on_audit_event(tree, {"delete"}):
            offenders.append(rel)
    assert not offenders, (
        f"these modules delete audit rows: {offenders} — audit_events is append-only "
        "(ADR 0041 §2.7). Only the retention sweep may delete, and it is already "
        "listed in _DELETE_ALLOWED."
    )


def test_nothing_updates_an_audit_row() -> None:
    """An event records something that already happened, so a correction is a NEW
    event, never an edit — otherwise the log can be quietly rewritten to say
    something else happened, which is worse than having no log at all.
    """
    offenders = []
    for path in _python_sources():
        rel = str(path.relative_to(_APP))
        if rel in _UPDATE_ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        if _calls_on_audit_event(tree, {"update"}):
            offenders.append(rel)
    assert not offenders, (
        f"these modules update audit rows: {offenders} — audit_events is append-only. "
        "A correction is a new event. If G2 erasure needs to pseudonymize "
        "`actor_label` in place, add it to _UPDATE_ALLOWED with its justification."
    )


def test_no_module_assigns_to_a_loaded_audit_event() -> None:
    """The ORM route to the same damage: loading a row and assigning to a field."""
    offenders: list[str] = []
    for path in _python_sources():
        rel = str(path.relative_to(_APP))
        if rel in _UPDATE_ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"event", "audit_event", "audit"}
                ):
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"these lines assign to a field of a loaded audit event: {offenders} — "
        "audit_events is append-only (ADR 0041 §2.7)."
    )


def test_the_guard_can_actually_see_a_violation() -> None:
    """The guard's own guard."""
    violation = ast.parse("from x import AuditEvent\nsession.execute(delete(AuditEvent))\n")
    assert _calls_on_audit_event(violation, {"delete"}) is True

    innocent = ast.parse("session.execute(delete(Check))\n")
    assert _calls_on_audit_event(innocent, {"delete"}) is False

    # …and the file list is non-empty, so the loops above ran at all.
    assert len(_python_sources()) > 50
