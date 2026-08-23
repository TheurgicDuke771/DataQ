"""Strip URI credentials out of assets.namespace and connections.config (#754, #826)."""

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
    """`scheme://user:pass@host/db` → `scheme://user@host/db` (username kept)."""
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
    """No-op — see the module docstring."""
