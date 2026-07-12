"""Strip URI credentials out of assets.namespace and connections.config (#754, #826).

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-07-12

The Iceberg SQL-catalog password was carried inline in `connections.config.catalog_uri`
(the connection type had one secret slot, taken by the storage key), and
`asset_identity._resolve_iceberg` copied that URI **verbatim** into `assets.namespace`.
The credential was therefore persisted in two plaintext columns, served by the read API,
rendered in the UI, shipped to catalogs inside lineage query strings, and logged.

The code no longer produces this (the config now refuses a password in `catalog_uri`,
and the namespace is derived credential-free). This migration repairs the rows already
written.

**`assets.namespace` is UPDATED IN PLACE — deliberately.** The namespace is half of the
asset's identity, so writing a *new* credential-free row instead would fork the asset:
the old row (and every suite/run/lineage_edge/incident pointing at its id) would be
orphaned, and the UI would show a duplicate. Rewriting the existing row keeps `assets.id`
stable, so every foreign key follows automatically.

Uniqueness: `uq_assets_namespace_name` could in principle be violated if a credential-free
twin of a poisoned row already exists (e.g. someone re-created the connection correctly).
We therefore fold rather than collide — see `_merge_or_rewrite`.

**Down-migration cannot restore the credential, and must not.** Reversing this would mean
re-inserting a password into a plaintext column; the downgrade is intentionally a no-op
(the credential-free namespace is valid input to every reader), so a rollback is safe but
does not resurrect the leak.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def _strip(uri: str) -> str:
    """`scheme://user:pass@host/db` → `scheme://user@host/db` (username kept).

    Duplicated from `core.uri_credentials.strip_uri_credentials` on purpose: a migration
    must be pinned to the schema/logic of its own moment and keep working even if the
    application helper is later renamed or changed.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    if not parts.password:
        return uri
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _merge_or_rewrite(conn: sa.Connection, asset_id: str, new_ns: str, name: str) -> None:
    """Point the asset at its credential-free namespace, folding onto a twin if one exists."""
    twin = conn.execute(
        sa.text("SELECT id FROM assets WHERE namespace = :ns AND name = :name AND id <> :id"),
        {"ns": new_ns, "name": name, "id": asset_id},
    ).scalar()
    if twin is None:
        conn.execute(
            sa.text("UPDATE assets SET namespace = :ns WHERE id = :id"),
            {"ns": new_ns, "id": asset_id},
        )
        return

    # A credential-free twin already exists: re-point everything that referenced the
    # poisoned row at it, then drop the poisoned row. Never leave dangling FKs.
    for table, col in (
        ("suites", "asset_id"),
        ("runs", "asset_id"),
        ("incidents", "asset_id"),
        ("lineage_edges", "upstream_asset_id"),
        ("lineage_edges", "downstream_asset_id"),
    ):
        conn.execute(
            sa.text(f"UPDATE {table} SET {col} = :twin WHERE {col} = :old"),  # noqa: S608
            {"twin": twin, "old": asset_id},
        )
    conn.execute(sa.text("DELETE FROM assets WHERE id = :id"), {"id": asset_id})


def upgrade() -> None:
    conn = op.get_bind()

    # 1) assets.namespace — the identity that reached the UI.
    rows = conn.execute(
        sa.text("SELECT id, namespace, name FROM assets WHERE namespace LIKE '%:%@%'")
    ).fetchall()
    for asset_id, namespace, name in rows:
        stripped = _strip(namespace)
        if stripped != namespace:
            _merge_or_rewrite(conn, asset_id, stripped, name)

    # 2) connections.config — any URI-shaped value carrying a password, any type.
    #    Generic, so a non-Iceberg type that ever did the same thing is caught too.
    #    The credential itself is NOT discarded blindly: an operator must re-point the
    #    connection at a SecretStore entry (`*_secret_name`) — see the issue. This
    #    migration only stops the plaintext copy from sitting in the DB and the API.
    conns = conn.execute(sa.text("SELECT id, config FROM connections")).fetchall()
    for conn_id, config in conns:
        if not isinstance(config, dict):
            continue
        cleaned = {
            k: (_strip(v) if isinstance(v, str) and "://" in v and "@" in v else v)
            for k, v in config.items()
        }
        if cleaned != config:
            conn.execute(
                sa.text("UPDATE connections SET config = CAST(:cfg AS jsonb) WHERE id = :id"),
                {"cfg": json.dumps(cleaned), "id": conn_id},
            )


def downgrade() -> None:
    """No-op — see the module docstring.

    Reversing this would mean writing a password back into a plaintext column. The
    credential-free namespace/config is valid input for every reader, so a rollback is
    safe as-is; the leak is simply not resurrected.
    """
