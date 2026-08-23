"""Workspace-role policy — the coarse authorization axis (ADR 0033)."""

from __future__ import annotations

from backend.app.core.config import get_settings
from backend.app.db.models import ADMIN_ROLE, DEFAULT_WORKSPACE_ROLE, VIEWER_ROLE, User

# Ordered workspace-role ranks (ADR 0033).
ROLE_RANK = {VIEWER_ROLE: 1, DEFAULT_WORKSPACE_ROLE: 2, ADMIN_ROLE: 3}


def resolve_role(user: User) -> str:
    """The user's effective workspace role — `admin | member | viewer` (ADR 0033)."""
    if user.role == ADMIN_ROLE or get_settings().is_admin_email(user.email):
        return ADMIN_ROLE
    return user.role


def is_workspace_admin(user: User) -> bool:
    """True iff the user is a workspace admin — stored role OR allowlist (ADR 0033)."""
    return resolve_role(user) == ADMIN_ROLE


def bootstrap_role(email: str, *, default: str = DEFAULT_WORKSPACE_ROLE) -> str:
    """The role a sign-in should SEED a brand-new user row with (ADR 0033 dec. 6/8)."""
    return ADMIN_ROLE if get_settings().is_admin_email(email) else default


def should_promote_to_admin(email: str) -> bool:
    """Whether a sign-in should write an EXISTING row's role up to `admin`."""
    return get_settings().is_admin_email(email)


def admin_promotion_values(email: str) -> dict[str, str]:
    """`{"role": "admin"}` when this sign-in should promote, `{}` when it must not."""
    return {"role": ADMIN_ROLE} if should_promote_to_admin(email) else {}


__all__ = [
    # Re-exported from `db.models` so a gate-defining module has one import.
    "ADMIN_ROLE",
    "DEFAULT_WORKSPACE_ROLE",
    "ROLE_RANK",
    "VIEWER_ROLE",
    "admin_promotion_values",
    "bootstrap_role",
    "is_workspace_admin",
    "resolve_role",
    "should_promote_to_admin",
]
