"""Tags (#888): a workspace-shared vocabulary of labels, assigned to suites, assets, connections
and checks through one join table each. FastAPI-free.

Assignments are read with the entity (so a suite's tags are gated exactly as the suite is); the
vocabulary itself is workspace knowledge — any member may list it, only an Admin may delete from
it, and an editor creates a tag simply by assigning a name nobody has used yet.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, Table, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    TAG_NAME_MAX_LENGTH,
    Asset,
    Check,
    Connection,
    Suite,
    Tag,
    asset_tags,
    check_tags,
    connection_tags,
    suite_tags,
)
from backend.app.services import audit_service

log = get_logger(__name__)

#: Most tags one entity may carry; a filter is useless past this and the picker unreadable.
MAX_TAGS_PER_ENTITY = 50


class TagNameInvalidError(DataQError):
    status_code = 422
    code = "tag_name_invalid"


class TagNotFoundError(DataQError):
    status_code = 404
    code = "tag_not_found"


def normalize_tag_name(raw: str) -> str:
    """The stored form of a tag name: NFC, lower-case, trimmed, runs of whitespace collapsed to one
    space. 422 on empty, over-long or control characters — ``key:value`` stays legal so the later
    structured form is additive. ``lower()`` not ``casefold()``: the picker previews the stored
    form with JavaScript's ``toLowerCase``, and the two must agree (``ß`` stays ``ß``)."""
    name = " ".join(unicodedata.normalize("NFC", raw).lower().split())
    if not name:
        raise TagNameInvalidError("tag name is empty", detail={"name": raw})
    if len(name) > TAG_NAME_MAX_LENGTH:
        raise TagNameInvalidError(
            f"tag name is longer than {TAG_NAME_MAX_LENGTH} characters",
            detail={"name": raw, "max_length": TAG_NAME_MAX_LENGTH},
        )
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        raise TagNameInvalidError("tag name contains a control character", detail={"name": raw})
    return name


def normalize_tag_names(names: Iterable[str]) -> list[str]:
    """Normalized, de-duplicated, in first-seen order; 422 past ``MAX_TAGS_PER_ENTITY``."""
    out = list(dict.fromkeys(normalize_tag_name(n) for n in names))
    if len(out) > MAX_TAGS_PER_ENTITY:
        raise TagNameInvalidError(
            f"an entity may carry at most {MAX_TAGS_PER_ENTITY} tags",
            detail={"count": len(out), "max": MAX_TAGS_PER_ENTITY},
        )
    return out


def ensure_tags(session: Session, names: Sequence[str]) -> list[Tag]:
    """The ``Tag`` rows for already-normalized ``names``, creating the missing ones.

    ``INSERT … ON CONFLICT DO NOTHING`` then a re-select, so two editors assigning the same new
    name at once both succeed — a plain insert would raise on the unique index for the loser.
    """
    if not names:
        return []
    session.execute(
        pg_insert(Tag)
        .values([{"name": n} for n in names])
        .on_conflict_do_nothing(index_elements=[Tag.name])
    )
    rows = {t.name: t for t in session.scalars(select(Tag).where(Tag.name.in_(names)))}
    return [rows[n] for n in names]


def set_tags(
    session: Session, entity: Suite | Asset | Connection | Check, names: Iterable[str]
) -> bool:
    """Replace the entity's tags with ``names`` (raw — normalized here). Does not commit; the
    caller's own audit + commit covers it. Returns whether anything changed."""
    wanted = normalize_tag_names(names)
    if sorted(wanted) == sorted(t.name for t in entity.tags):
        return False
    entity.tags = ensure_tags(session, wanted)
    return True


def filter_by_tags(
    stmt: Select[Any], join_table: Table, entity_id_column: Any, tags: Sequence[str]
) -> Select[Any]:
    """Keep rows carrying EVERY tag in ``tags`` (AND): one membership test per name. A name no tag
    has matches nothing, which is the honest answer, not an error."""
    entity_fk = next(c for c in join_table.c if c.name != "tag_id")
    for raw in tags:
        name = normalize_tag_name(raw)
        tagged = (
            select(literal(1))
            .select_from(join_table.join(Tag, Tag.id == join_table.c.tag_id))
            .where(Tag.name == name, entity_fk == entity_id_column)
        )
        stmt = stmt.where(tagged.exists())
    return stmt


@dataclass(frozen=True)
class TagUsage:
    """One vocabulary entry with how many of each entity carry it — workspace-wide counts (the
    vocabulary is not grant-scoped), never which entities."""

    id: uuid.UUID
    name: str
    created_at: Any
    suites: int
    assets: int
    connections: int
    checks: int


def _count(join_table: Table) -> Any:
    return (
        select(func.count())
        .select_from(join_table)
        .where(join_table.c.tag_id == Tag.id)
        .correlate(Tag)
        .scalar_subquery()
    )


def _usage_select() -> Any:
    return select(
        Tag.id,
        Tag.name,
        Tag.created_at,
        _count(suite_tags),
        _count(asset_tags),
        _count(connection_tags),
        _count(check_tags),
    )


def list_tags(session: Session) -> list[TagUsage]:
    """Every tag, by name, with its usage counts."""
    rows = session.execute(_usage_select().order_by(Tag.name)).all()
    return [TagUsage(*row) for row in rows]


def tag_usage(session: Session, tag_id: uuid.UUID) -> TagUsage:
    row = session.execute(_usage_select().where(Tag.id == tag_id)).one_or_none()
    if row is None:
        raise TagNotFoundError("tag not found", detail={"tag_id": str(tag_id)})
    return TagUsage(*row)


def get_tag(session: Session, tag_id: uuid.UUID) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise TagNotFoundError("tag not found", detail={"tag_id": str(tag_id)})
    return tag


def delete_tag(session: Session, tag_id: uuid.UUID, *, actor_id: uuid.UUID | None) -> None:
    """Remove a tag from the vocabulary; the FK cascade detaches it from every entity."""
    tag = get_tag(session, tag_id)
    usage = tag_usage(session, tag_id)
    before = audit_service.snapshot("tag", tag)
    session.delete(tag)
    audit_service.record(
        session,
        action="tag.delete",
        entity_type="tag",
        entity_id=tag_id,
        actor=actor_id,
        before=before,
        after={
            "deleted": True,
            "detached": {
                "suites": usage.suites,
                "assets": usage.assets,
                "connections": usage.connections,
                "checks": usage.checks,
            },
        },
    )
    session.commit()
    log.info("tag_deleted", tag_id=str(tag_id), name=tag.name)
