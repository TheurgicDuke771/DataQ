"""Workspace-role policy — the coarse authorization axis (ADR 0033).

The pure rules: how the two admin sources compose into an effective role, and
what a sign-in may write back to `users.role`. FastAPI-free and framework-free —
it takes a `User` and reads `Settings`, nothing else.

**Why its own module rather than living in `core/auth.py`:** the OTP sign-in path
(`services/otp_service`) is a third writer of `users.role`, and `core.auth`
already imports `otp_service` for `normalize_email`. Putting the policy in
`core.auth` would make the import cycle unbreakable and push `otp_service` into a
function-level import — i.e. the shared rule would be *structurally* harder to
reach from one of the three places that must obey it, which is precisely how
three near-identical copies of it come about. `core.auth` re-exports these for
its FastAPI gates (`require_role`, `require_workspace_admin`), so callers may
import from either.

The two axes this composes with are documented in `services/suite_authz`: role
says what *kind* of user you are workspace-wide, a share says what you may touch
on one suite. Neither replaces the other.
"""

from __future__ import annotations

from backend.app.core.config import get_settings
from backend.app.db.models import ADMIN_ROLE, DEFAULT_WORKSPACE_ROLE, User

# Ordered workspace-role ranks (ADR 0033). Mirrors `suite_authz._RANK`'s shape so
# the two authz axes report and compare a level the same way — but they are
# deliberately SEPARATE ladders: `admin` here is workspace-admin, which the suite
# ladder then re-expresses as an implicit per-suite `admin` (ADR 0027).
#
# Written out rather than derived from `WORKSPACE_ROLES`' tuple order, which is
# alphabetical-by-accident and would silently invert the ladder if reordered.
# `test_role_rank_covers_every_stored_role` holds the two in sync instead.
ROLE_RANK = {"viewer": 1, DEFAULT_WORKSPACE_ROLE: 2, ADMIN_ROLE: 3}


def resolve_role(user: User) -> str:
    """The user's effective workspace role — `admin | member | viewer` (ADR 0033).

    The one place the two admin sources compose: the **stored** `users.role`
    (the ADR's coarse axis, #740) OR the `WORKSPACE_ADMIN_EMAILS` allowlist,
    which decision 6 demotes from *the* admin mechanism to a bootstrap seed and
    lockout break-glass. Allowlist membership is matched case-insensitively on
    the IdP-supplied email — a generic identity attribute, so no Azure/Entra
    claim is read here (ADR 0010/0013, CLAUDE.md §11).

    The allowlist can only ever *raise* the answer to `admin`, never lower it:
    an operator who removes themselves from the env keeps whatever role is
    stored, which is what makes the env path recoverable rather than a second
    source of truth that can silently demote people.

    Resolved per request, deliberately — no caching, no session material. A role
    change therefore takes effect on the target's very next request, including
    requests made with their PATs, since a PAT authenticates *as its user*
    (ADR 0026): demote the user and the token demotes with them, with no token
    revocation machinery to build or to get wrong.

    Reads `get_settings()` (not an import-time singleton) so a test can vary the
    allowlist with `get_settings.cache_clear()`; in a running process settings
    are read once at startup (12-factor — change the env and restart).
    """
    if user.role == ADMIN_ROLE or get_settings().is_admin_email(user.email):
        return ADMIN_ROLE
    return user.role


def is_workspace_admin(user: User) -> bool:
    """True iff the user is a workspace admin — stored role OR allowlist (ADR 0033).

    Workspace admin is the whole-workspace administrator, distinct from the
    per-suite view/edit/admin/owner ladder in `suite_authz` (on which they are
    an implicit `admin` on every suite — ADR 0027, unchanged; only its *source*
    moved here). Thin alias over `resolve_role` so the two can never drift.
    """
    return resolve_role(user) == ADMIN_ROLE


def bootstrap_role(email: str, *, default: str = DEFAULT_WORKSPACE_ROLE) -> str:
    """The role a sign-in should SEED a brand-new user row with (ADR 0033 dec. 6/8).

    `admin` if the address is on the `WORKSPACE_ADMIN_EMAILS` allowlist, else
    `default` — the caller's signup default (`AUTH_OIDC_DEFAULT_ROLE` for the
    OIDC/AAD path, `AUTH_OTP_DEFAULT_ROLE` for OTP signups). This encodes the
    ADR's precedence rule in ONE place: **the allowlist write-through wins over
    any signup default**, so an operator on both lists is stored `admin` at first
    sign-in rather than `member`-stored-but-admin-effective — which is what stops
    a later env-entry removal from silently demoting the bootstrap admin that
    #742's last-admin guard counts.

    Seeding only. `should_promote_to_admin` is the separate rule for rows that
    already exist.
    """
    return ADMIN_ROLE if get_settings().is_admin_email(email) else default


def should_promote_to_admin(email: str) -> bool:
    """Whether a sign-in should write an EXISTING row's role up to `admin`.

    A predicate rather than a `promoted_role(email, stored) -> str` function,
    because all three call sites need to know whether to write *at all*, not what
    value to write. Two of them build SQL (`insert(...).on_conflict_do_update`,
    `update(...)`) where "keep the stored role" must mean **omit the column**:
    writing the column back as itself would make a per-request sign-in upsert
    participate in a lost-update race with a concurrent in-app role change it has
    no opinion about.

    True iff the address is on the `WORKSPACE_ADMIN_EMAILS` allowlist. The
    asymmetry — promote, never demote — is load-bearing rather than cautious: if
    removal from the env allowlist also demoted the stored row, the break-glass
    path could *take* admin away as well as grant it, and #742's last-admin guard
    (which deliberately counts stored-role admins only) could be talked out of
    its invariant by an env edit plus one sign-in. Demotion has exactly one
    sanctioned route: `PATCH /admin/users/{id}/role`, where the guard runs.
    """
    return get_settings().is_admin_email(email)


def admin_promotion_values(email: str) -> dict[str, str]:
    """`{"role": "admin"}` when this sign-in should promote, `{}` when it must not.

    The spread-into-SQL form of `should_promote_to_admin`, so both statement
    builders in `core.auth` express "promote-only" identically instead of each
    spelling out its own conditional dict.
    """
    return {"role": ADMIN_ROLE} if should_promote_to_admin(email) else {}


__all__ = [
    "ROLE_RANK",
    "admin_promotion_values",
    "bootstrap_role",
    "is_workspace_admin",
    "resolve_role",
    "should_promote_to_admin",
]
