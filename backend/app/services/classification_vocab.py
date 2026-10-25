"""The column-classification vocabulary — a leaf module with no DataQ imports.

Both `column_tags` (the warehouse source + the effective map) and `column_detections` (the
profiler source, ADR 0049) speak it, and `column_tags` reads `column_detections` lazily when it
builds the map; keeping the shared words here is what keeps that edge one-directional.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: Normalized outputs of every classification source.
SENSITIVE: Final[str] = "sensitive"
NON_SENSITIVE: Final[str] = "public"
#: A profiler detection (ADR 0049), overlaid by `effective_column_tags` on columns with no
#: warehouse verdict. Deliberately a THIRD value: it is in neither the sensitive-tag set (so it
#: is not the floor — the suite's `identifier_column` clears it) nor the clearing set (so it
#: never satisfies fail-closed clearance).
DETECTED: Final[str] = "detected"


def fold_key(column: str) -> str:
    """The redaction map's key for a column name: stripped and lower-cased, the one folding every
    rung (warehouse tags, inheritance, detections, the policy lookups) agrees on."""
    return column.strip().lower()


class EffectiveColumnTags(dict[str, str]):
    """The column→verdict map plus WHICH `sensitive` entries arrived through lineage rather than
    from the column's own warehouse tag (#2255). A plain dict to every existing reader — equality,
    `.get`, JSON — so the provenance can never change a verdict; the redaction reasons read it."""

    __slots__ = ("inherited",)

    def __init__(self, tags: dict[str, str], *, inherited: frozenset[str] = frozenset()) -> None:
        super().__init__(tags)
        self.inherited = inherited


def copy_tags(tags: Mapping[str, str] | None) -> EffectiveColumnTags:
    """A mutable copy of a column→verdict map that KEEPS the lineage provenance — `dict(tags)`
    or `{**tags}` would silently drop it, and an inherited mask would then report as `own_tag`."""
    return EffectiveColumnTags(dict(tags or {}), inherited=inherited_tag_columns(tags))


def inherited_tag_columns(tags: object) -> frozenset[str]:
    """The folded column names whose verdict was inherited, or an empty set for a plain map."""
    return getattr(tags, "inherited", frozenset()) if isinstance(tags, dict) else frozenset()
