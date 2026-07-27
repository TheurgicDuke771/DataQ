"""Rename legacy ``conn-<uuid>`` vault keys to the readable ``conn-<env>-<slug>-<id>``.

**ONE-SHOT — DELETE ME (#1060).** This is not product code. The rename is optional:
`Connection.secret_ref` is a stored column that is never recomputed, so a legacy
`conn-<uuid>` ref resolves indefinitely and nothing breaks if this never runs. It
is tracked only so the run that rewrites production Key Vault is a reviewed and
tested one; remove it (and its tests) once prod has been migrated.

Run against whatever `SECRET_STORE` the environment selects, so the same script
serves the local OpenBao and production Key Vault.

    python -m backend.scripts.migrate_secret_names            # dry run (default)
    python -m backend.scripts.migrate_secret_names --apply

**Why the order of operations is what it is.** #954 is the reference incident: a
partial credential rotation left two Snowflake connections dead for three weeks
because some copies were updated and nobody verified the rest. This script is
built so that every intermediate state is a *working* state:

    1. read the value from the OLD key
    2. write it to the NEW key
    3. read the NEW key back and compare — the write is not trusted, it is checked
    4. commit the new `secret_ref` to Postgres
    5. only then purge the OLD key

Fail anywhere before step 4 and the database still points at the old key, which
still holds the credential — the connection keeps working and the run is safely
retryable. Fail at step 5 and the credential is duplicated but the database
points at the verified new copy, so the connection still works and the leftover
is logged by name for manual cleanup rather than silently abandoned.

Idempotent: a ref that is already readable is skipped, so re-running is a no-op.
Secret VALUES are never printed, logged, or written to disk — only key names.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import configure_logging, get_logger
from backend.app.core.secret_names import connection_secret_ref, is_readable_ref
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStore,
    SecretStoreUnavailableError,
    SecretWriteError,
    get_secret_store,
)
from backend.app.db.models import Connection
from backend.app.db.session import SessionLocal

log = get_logger(__name__)


class MigrationError(Exception):
    """A single connection could not be migrated; the run continues with the rest."""


def _migrate_one(
    session: Session, conn: Connection, store: SecretStore, *, apply: bool
) -> tuple[str, str]:
    """Return (old_ref, new_ref). Raises `MigrationError` on any unsafe condition."""
    old_ref = conn.secret_ref
    assert old_ref is not None  # caller filters
    new_ref = connection_secret_ref(connection_id=conn.id, env=conn.env, name=conn.name)

    if new_ref == old_ref:  # pragma: no cover — defensive
        raise MigrationError("computed ref equals the existing one")

    if not apply:
        return old_ref, new_ref

    # 1. read the old value
    try:
        value = store.get(old_ref)
    except SecretNotFoundError as exc:
        # The row points at a key that does not exist — a pre-existing inconsistency
        # (#1059). Renaming would fabricate nothing; leave it for a human.
        raise MigrationError(f"old key absent, nothing to copy: {exc}") from exc
    except SecretStoreUnavailableError as exc:
        raise MigrationError(f"store unavailable, retry later: {exc}") from exc

    # 2. write it under the new name
    try:
        store.set(new_ref, value)
    except SecretWriteError as exc:
        raise MigrationError(f"could not write the new key: {exc}") from exc

    # 3. VERIFY — do not trust the write. A silent mismatch here is exactly the
    #    #954 shape: a rename that reports success while the credential is wrong.
    try:
        written = store.get(new_ref)
    except (SecretNotFoundError, SecretStoreUnavailableError) as exc:
        raise MigrationError(f"new key unreadable after write: {exc}") from exc
    if written != value:
        raise MigrationError("read-back mismatch — new key does NOT hold the old value")

    # 4. repoint the row, and commit BEFORE deleting anything
    conn.secret_ref = new_ref
    session.commit()

    # 5. purge the old copy. A failure here leaves a duplicate, not a broken
    #    connection — logged by name so it can be cleaned up by hand.
    store.delete(old_ref)  # fail-soft by contract
    return old_ref, new_ref


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration (default is a dry run that changes nothing)",
    )
    args = parser.parse_args(argv)
    configure_logging()

    store = get_secret_store()
    session = SessionLocal()
    migrated: list[tuple[str, str]] = []
    skipped = 0
    failures: list[tuple[str, str]] = []

    try:
        conns = list(session.scalars(select(Connection).where(Connection.secret_ref.isnot(None))))
        for conn in conns:
            if is_readable_ref(conn.secret_ref or ""):
                skipped += 1
                continue
            try:
                old_ref, new_ref = _migrate_one(session, conn, store, apply=args.apply)
            except MigrationError as exc:
                session.rollback()
                failures.append((str(conn.id), str(exc)))
                log.warning("secret_rename_failed", connection_id=str(conn.id), error=str(exc))
                continue
            migrated.append((old_ref, new_ref))
    finally:
        session.close()

    verb = "renamed" if args.apply else "would rename"
    print(
        f"\n{verb} {len(migrated)} secret(s); skipped {skipped} already-readable; "
        f"{len(failures)} failed\n"
    )
    for old_ref, new_ref in migrated:
        print(f"  {old_ref}\n    -> {new_ref}")
    for conn_id, why in failures:
        print(f"  FAILED connection {conn_id}: {why}")
    if not args.apply and migrated:
        print("\nDry run — nothing changed. Re-run with --apply.")
    # Non-zero on failure so CI/automation notices; a dry run is always success.
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
