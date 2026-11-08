"""Re-key flat-file batch-target assets to include the target's prefix (#2481)

A batch target's asset name used to ignore the target's listing `prefix`, so two suites reading
different folders with similar file patterns (`orders/` + `orders_(\\d+)\\.csv` and `sales/` +
`orders_(\\d+)\\.csv`) shared one asset (`orders_`), merging their scorecards, lineage and
incidents. The name is now the prefix joined with the pattern's literal part, cut at the last `/`.
Only suites whose batch target sets a prefix get a different name.

For each asset such a suite points at:
- every suite on it maps to the same new name, and no asset holds that name yet: the asset is
  renamed in place, keeping its tags, classifications, lineage and settings;
- otherwise (the asset merged several folders, or the new name exists): each group of suites is
  moved to the asset with its new name, found or created, together with the runs and incidents
  it recorded on the old one. Lineage edges and classifications stay on the old asset; the next
  lineage pull and profile write them for the new one, and an old asset nothing references any
  more is removed by the orphan sweep.

Data only. Rollback: `alembic downgrade -1` is a no-op; a split cannot be told apart from assets
that were always separate, and the older code keeps reading the re-keyed rows.
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "d9d6d88d6642"
down_revision: str | None = "ad8a8c3394b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of `asset_identity._pattern_base_prefix` at this revision.
_REGEX_METACHARS = re.compile(r"[\\.^$*+?{}\[\]|()]")
_FLATFILE_TYPES = ("adls_gen2", "s3", "gcs")


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def batch_asset_name(target: dict[str, Any]) -> str | None:
    """The asset name of a batch target, or None for any other target."""
    if _text(target.get("path")):
        return None
    pattern = _text(target.get("pattern"))
    if pattern is None:
        return None
    match = _REGEX_METACHARS.search(pattern)
    literal = (pattern[: match.start()] if match else pattern).lstrip("/")
    listed = (_text(target.get("prefix")) or "").lstrip("/")
    if listed and not literal.startswith(listed):
        literal = listed + literal
    if "/" in literal:
        return literal[: literal.rfind("/") + 1]
    return literal or pattern.lstrip("/")


def _asset_id(bind: Any, asset: Any, name: str) -> uuid.UUID:
    existing = bind.scalar(
        sa.text("SELECT id FROM assets WHERE namespace = :ns AND name = :name"),
        {"ns": asset.namespace, "name": name},
    )
    if existing is not None:
        return existing
    new_id = uuid.uuid4()
    bind.execute(
        sa.text(
            "INSERT INTO assets (id, namespace, name, env, connection_id)"
            " VALUES (:id, :ns, :name, :env, :conn)"
        ),
        {
            "id": new_id,
            "ns": asset.namespace,
            "name": name,
            "env": asset.env,
            "conn": asset.connection_id,
        },
    )
    return new_id


def _move(bind: Any, suite_ids: list[uuid.UUID], old: uuid.UUID, new: uuid.UUID) -> None:
    params = {"suites": suite_ids, "old": old, "new": new}
    bind.execute(sa.text("UPDATE suites SET asset_id = :new WHERE id = ANY(:suites)"), params)
    for table in ("runs", "incidents"):
        bind.execute(
            sa.text(
                f"UPDATE {table} SET asset_id = :new"  # noqa: S608  # nosec B608
                " WHERE suite_id = ANY(:suites) AND asset_id = :old"
            ),
            params,
        )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT s.id AS suite_id, s.target, a.id AS asset_id, a.namespace, a.name, a.env,"
            " a.connection_id"
            " FROM suites s"
            " JOIN connections c ON c.id = s.connection_id"
            " JOIN assets a ON a.id = s.asset_id"
            " WHERE c.type = ANY(:types)"
        ),
        {"types": list(_FLATFILE_TYPES)},
    ).all()
    assets: dict[uuid.UUID, Any] = {}
    groups: dict[uuid.UUID, dict[str, list[uuid.UUID]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        assets[row.asset_id] = row
        name = batch_asset_name(row.target or {}) or row.name
        groups[row.asset_id][name].append(row.suite_id)
    for asset_id, by_name in groups.items():
        asset = assets[asset_id]
        if set(by_name) == {asset.name}:
            continue
        if len(by_name) == 1:
            (name,) = by_name
            taken = bind.scalar(
                sa.text("SELECT 1 FROM assets WHERE namespace = :ns AND name = :name"),
                {"ns": asset.namespace, "name": name},
            )
            if not taken:
                bind.execute(
                    sa.text("UPDATE assets SET name = :name WHERE id = :id"),
                    {"name": name, "id": asset_id},
                )
                continue
        for name, suite_ids in by_name.items():
            if name != asset.name:
                _move(bind, suite_ids, asset_id, _asset_id(bind, asset, name))


def downgrade() -> None:
    pass
