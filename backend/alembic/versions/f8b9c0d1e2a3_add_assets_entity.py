"""add assets entity + suite/run asset_id linkage + target backfill (G-d phase 1)

ADR 0034 (gap G-d): promote "the table" — implicit today inside `Suite.target`
JSONB — to a first-class `assets` row that lineage edges, incidents, an asset
page, and catalog sync can all reference. Identity = the OpenLineage dataset
naming spec (`namespace` + `name`, unique together), adopted verbatim.

Additive & backward-compatible (CLAUDE.md migration rules): a brand-new table
plus two **nullable** FK columns (`suites.asset_id`, `runs.asset_id`, both
`ON DELETE SET NULL`). No existing read path breaks; the columns start NULL and
are populated by the resolver hooks going forward, so the code that reads them
(ADR 0034 build set) can ship in a later PR.

Backfill: every existing suite with a non-null target is resolved to an asset
row and linked. Resolution is **fail-soft** — a suite whose config/target can't
be resolved (bad/legacy shape, orchestration-type connection) is skipped with
its `asset_id` left NULL, exactly mirroring the runtime hook. Runs are **not**
backfilled: run history records the asset a run actually ran against and must not
be rewritten from a suite's current target (a design decision, ADR 0034).

The backfill logic below is a **deliberately frozen, self-contained copy** of
`app/services/asset_identity.resolve_asset_identity` as of this revision — a
migration must not import app code (the app evolves; a migration is a fixed
historical step). Keep the two in sync only by re-freezing in a *new* migration
if the identity rules ever change.

Revision ID: f8b9c0d1e2a3
Revises: e716a1b2c3d4
Create Date: 2026-07-10 00:00:00.000000+00:00

"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = "f8b9c0d1e2a3"
down_revision: str | None = "e716a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── Frozen copy of asset_identity.resolve_asset_identity (see module docstring) ──
# `pattern` is a regex (flatfile.py `re.compile`s it), not a glob.
_REGEX_METACHARS = re.compile(r"[\\.^$*+?{}\[\]|()]")


def _normalize_snowflake_account(account: str) -> str:
    # openlineage `fix_account_name`: hyphen check scoped to the first dot-segment
    # (org-account form returns only that segment); locator regions legitimately
    # contain hyphens (`us-east-1`) so must still get `.aws` appended.
    account = account.strip()
    if not account:
        raise ValueError("empty account")
    parts = account.split(".")
    if "-" in parts[0]:
        return parts[0]
    if len(parts) == 1:
        return f"{parts[0]}.us-west-1.aws"
    if len(parts) == 2:
        return f"{parts[0]}.{parts[1]}.aws"
    return account


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _require(d: dict[str, Any], field: str, conn_type: str, kind: str) -> str:
    value = d.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{conn_type} missing {kind} {field!r}")
    return value


def _url_host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/", 1)[0]


def _normalize_part(part: str, *, engine: str) -> str:
    quote_chars = ('"',) if engine == "snowflake" else ('"', "`")
    for quote in quote_chars:
        if len(part) >= 2 and part.startswith(quote) and part.endswith(quote):
            inner = part[1:-1]
            if not inner:
                raise ValueError("empty quoted identifier part")
            return inner
    return part.upper() if engine == "snowflake" else part.lower()


def _pattern_base_prefix(pattern: str) -> str:
    match = _REGEX_METACHARS.search(pattern)
    prefix = pattern[: match.start()] if match else pattern
    if "/" in prefix:
        base = prefix[: prefix.rfind("/") + 1]
    elif prefix:
        base = prefix
    else:
        base = pattern
    return base.lstrip("/")


def _flatfile_name(target: dict[str, Any]) -> str:
    path = _str_or_none(target.get("path"))
    if path:
        return path.lstrip("/")
    pattern = _str_or_none(target.get("pattern"))
    if pattern:
        return _pattern_base_prefix(pattern)
    raise ValueError("flat-file target requires 'path' or 'pattern'")


def _resolve_identity(
    conn_type: str, config: dict[str, Any], target: dict[str, Any]
) -> tuple[str, str]:
    """Return ``(namespace, name)`` or raise ``ValueError`` — frozen copy of
    `asset_identity.resolve_asset_identity` as of this revision."""
    if conn_type == "snowflake":
        account = _require(config, "account", "snowflake", "config")
        database = _require(config, "database", "snowflake", "config")
        schema = _str_or_none(target.get("schema")) or _str_or_none(config.get("schema"))
        if not schema:
            raise ValueError("snowflake requires a schema")
        table = _require(target, "table", "snowflake", "target")
        namespace = f"snowflake://{_normalize_snowflake_account(account)}"
        name = ".".join(_normalize_part(p, engine="snowflake") for p in (database, schema, table))
        return namespace, name
    if conn_type == "unity_catalog":
        workspace_url = _require(config, "workspace_url", "unity_catalog", "config")
        netloc = _url_host(workspace_url)
        if not netloc:
            raise ValueError("unity_catalog requires a valid workspace_url")
        catalog = _require(target, "catalog", "unity_catalog", "target")
        schema = _str_or_none(target.get("schema")) or "default"
        table = _require(target, "table", "unity_catalog", "target")
        namespace = f"unitycatalog://{netloc}"
        name = ".".join(
            _normalize_part(p, engine="unity_catalog") for p in (catalog, schema, table)
        )
        return namespace, name
    if conn_type == "adls_gen2":
        container = _require(config, "container", "adls_gen2", "config")
        account_url = _require(config, "account_url", "adls_gen2", "config")
        host = _url_host(account_url)
        account = host.split(".")[0] if host else ""
        if not account:
            raise ValueError("adls_gen2 requires a valid account_url")
        return f"abfss://{container}@{account}.dfs.core.windows.net", _flatfile_name(target)
    if conn_type == "s3":
        bucket = _require(config, "bucket", "s3", "config")
        return f"s3://{bucket}", _flatfile_name(target)
    if conn_type == "iceberg":
        catalog_uri = config.get("catalog_uri")
        namespace = (
            catalog_uri.strip() if isinstance(catalog_uri, str) and catalog_uri.strip() else "file"
        )
        table = _require(target, "table", "iceberg", "target")
        ns_part = _str_or_none(target.get("namespace"))
        name = f"{ns_part}.{table}" if ns_part else table
        return namespace, name
    raise ValueError(f"connection type {conn_type!r} has no asset identity")


def _backfill_assets() -> None:
    """Resolve every existing targeted suite to an asset row and link it.

    Insert-or-reuse keyed on ``(namespace, name)`` (dedup across suites that
    share a target); env + connection_id come from the suite's connection.
    Unresolvable suites are skipped (asset_id left NULL) — fail-soft."""
    bind = op.get_bind()
    suites = (
        bind.execute(
            sa.text(
                "SELECT s.id AS suite_id, s.target AS target, "
                "c.type AS conn_type, c.config AS config, "
                "c.id AS connection_id, c.env AS env "
                "FROM suites s JOIN connections c ON c.id = s.connection_id "
                "WHERE s.target IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )

    for row in suites:
        target = row["target"] or {}
        config = row["config"] or {}
        try:
            namespace, name = _resolve_identity(row["conn_type"], config, target)
        except Exception as exc:
            # fail-soft: bad/legacy target → leave asset_id NULL (mirrors runtime).
            logger.info("asset backfill skipped suite %s: %s", row["suite_id"], exc)
            continue

        asset_id = bind.execute(
            sa.text(
                "INSERT INTO assets (namespace, name, env, connection_id) "
                "VALUES (:namespace, :name, :env, :connection_id) "
                "ON CONFLICT (namespace, name) DO UPDATE "
                "SET last_seen = now(), env = EXCLUDED.env, "
                "connection_id = EXCLUDED.connection_id "
                "RETURNING id"
            ),
            {
                "namespace": namespace,
                "name": name,
                "env": row["env"],
                "connection_id": row["connection_id"],
            },
        ).scalar_one()

        bind.execute(
            sa.text("UPDATE suites SET asset_id = :asset_id WHERE id = :suite_id"),
            {"asset_id": asset_id, "suite_id": row["suite_id"]},
        )


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("env", sa.String(length=16), nullable=True),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "name", name="uq_assets_namespace_name"),
    )

    # ── suites.asset_id (nullable FK, SET NULL) ──
    op.add_column("suites", sa.Column("asset_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_suites_asset_id", "suites", "assets", ["asset_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_suites_asset_id", "suites", ["asset_id"])

    # ── runs.asset_id (nullable FK, SET NULL) ──
    op.add_column("runs", sa.Column("asset_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_runs_asset_id", "runs", "assets", ["asset_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_runs_asset_id", "runs", ["asset_id"])

    # ── backfill existing suites (runs stay NULL — history isn't rewritten) ──
    _backfill_assets()


def downgrade() -> None:
    op.drop_index("ix_runs_asset_id", table_name="runs")
    op.drop_constraint("fk_runs_asset_id", "runs", type_="foreignkey")
    op.drop_column("runs", "asset_id")

    op.drop_index("ix_suites_asset_id", table_name="suites")
    op.drop_constraint("fk_suites_asset_id", "suites", type_="foreignkey")
    op.drop_column("suites", "asset_id")

    op.drop_table("assets")
