"""Re-key flat-file batch-target assets to include the target's prefix (#2481)

A batch target's asset name used to ignore the target's listing `prefix`, so two suites reading
different folders with similar file patterns (`orders/` + `orders_(\\d+)\\.csv` and `sales/` +
`orders_(\\d+)\\.csv`) shared one asset (`orders_`), merging their scorecards, lineage and
incidents. The name is now the prefix joined with the pattern's literal part, cut at the last `/`.
Only suites whose batch target sets a prefix get a different name.

Every destination is planned before anything is written. For each asset such a suite points at:
- every suite on it maps to the same new name, no other suite has runs or incidents on it, and
  that name is free once the planned renames are done: the asset is renamed in place, keeping
  its tags, classifications, lineage and settings. Renames go through temporary names, so two
  assets may swap or chain names;
- otherwise (the asset merged several folders, holds another suite's history, or the name stays
  taken): each group of suites is moved to the asset with its new name, found or created, with
  the runs and incidents it recorded on the old one. History of a suite that no longer points
  at the asset stays where it was recorded. Lineage edges and classifications stay on the old
  asset; the next lineage pull and profile write them for the new one, and an old asset nothing
  references any more is removed by the orphan sweep.

An active incident is not moved onto an asset where its check already has an active one (the
`uq_incidents_active_asset_check` index): it stays on the old asset, and no incident changes
state.

Data only. Rollback: `alembic downgrade -1` is a no-op; a split cannot be told apart from assets
that were always separate, and the older code keeps reading the re-keyed rows.
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, cast

import sqlalchemy as sa

from alembic import op

revision: str = "d9d6d88d6642"
down_revision: str | None = "ad8a8c3394b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of `asset_identity._pattern_base_prefix` at this revision.
_REGEX_METACHARS = re.compile(r"[\\.^$*+?{}\[\]|()]")
_FLATFILE_TYPES = ("adls_gen2", "s3", "gcs")
# Frozen copy of `INCIDENT_ACTIVE_STATUSES`.
_ACTIVE = ("open", "acknowledged")


def _text(value: Any) -> str | None:
    """`asset_identity._str_or_none`: a nonblank string is kept byte for byte, never stripped."""
    return value if isinstance(value, str) and value.strip() else None


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
        return cast(uuid.UUID, existing)
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
    params = {"suites": suite_ids, "old": old, "new": new, "active": list(_ACTIVE)}
    bind.execute(sa.text("UPDATE suites SET asset_id = :new WHERE id = ANY(:suites)"), params)
    bind.execute(
        sa.text(
            "UPDATE runs SET asset_id = :new WHERE suite_id = ANY(:suites) AND asset_id = :old"
        ),
        params,
    )
    bind.execute(
        sa.text(
            "UPDATE incidents SET asset_id = :new"
            " WHERE suite_id = ANY(:suites) AND asset_id = :old"
            " AND NOT (status = ANY(:active) AND EXISTS ("
            "  SELECT 1 FROM incidents d WHERE d.asset_id = :new"
            "  AND d.check_id = incidents.check_id AND d.status = ANY(:active)))"
        ),
        params,
    )


def _history_suites(bind: Any, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    found: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for row in bind.execute(
        sa.text(
            "SELECT asset_id, suite_id FROM runs WHERE asset_id = ANY(:ids)"
            " UNION SELECT asset_id, suite_id FROM incidents WHERE asset_id = ANY(:ids)"
        ),
        {"ids": asset_ids},
    ):
        found[row.asset_id].add(row.suite_id)
    return found


def _plan_renames(
    bind: Any,
    assets: dict[uuid.UUID, Any],
    groups: dict[uuid.UUID, dict[str, list[uuid.UUID]]],
) -> dict[uuid.UUID, str]:
    """The assets renamed in place, each to a name no other asset holds once all are done."""
    history = _history_suites(bind, list(groups))
    renames: dict[uuid.UUID, str] = {}
    for asset_id, by_name in groups.items():
        pointing = {s for suites in by_name.values() for s in suites}
        if len(by_name) == 1 and set(by_name) != {assets[asset_id].name}:
            if not history.get(asset_id, set()) - pointing:
                (renames[asset_id],) = by_name
    namespaces = list({a.namespace for a in assets.values()})
    held = bind.execute(
        sa.text("SELECT id, namespace, name FROM assets WHERE namespace = ANY(:ns)"),
        {"ns": namespaces},
    ).all()
    while True:
        staying = {(r.namespace, r.name) for r in held if r.id not in renames}
        wanted: dict[tuple[str, str], int] = defaultdict(int)
        for asset_id, name in renames.items():
            wanted[(assets[asset_id].namespace, name)] += 1
        blocked = [
            asset_id
            for asset_id, name in renames.items()
            if (key := (assets[asset_id].namespace, name)) in staying or wanted[key] > 1
        ]
        if not blocked:
            return renames
        for asset_id in blocked:
            del renames[asset_id]


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

    renames = _plan_renames(bind, assets, groups)
    for asset_id in renames:
        bind.execute(
            sa.text("UPDATE assets SET name = :name WHERE id = :id"),
            {"name": f"rekey-2481-{asset_id.hex}", "id": asset_id},
        )
    for asset_id, name in renames.items():
        bind.execute(
            sa.text("UPDATE assets SET name = :name WHERE id = :id"), {"name": name, "id": asset_id}
        )
    # Every name is final now, so a destination found below is never renamed after it.
    for asset_id, by_name in groups.items():
        if asset_id in renames:
            continue
        asset = assets[asset_id]
        for name, suite_ids in by_name.items():
            if name != asset.name:
                _move(bind, suite_ids, asset_id, _asset_id(bind, asset, name))


def downgrade() -> None:
    pass
