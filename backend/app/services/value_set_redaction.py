"""A check's value set over a column that is sensitive NOW is withheld on read (#2592).

A value set (`config.value_set`, on `expect_column_values_to_be_in_set` and every other type that
stores one) is the check's definition, but its literals are values of the tested column. Once that
column is classified as sensitive (the suite's policy, a warehouse tag, fail-closed mode, or an
affirmative PII signal) they are held to the bar a failing sample of it is: each value is replaced
by the redaction marker, the list keeps its length, and the read names the column and the rung
that decided."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from backend.app.db.models import Asset, Suite
from backend.app.services import column_tags
from backend.app.services.value_set_guard import REDACTED_MARKER, VALUE_SET_KEY

if TYPE_CHECKING:
    from backend.app.services.run_service import RedactionReason


@dataclass(frozen=True)
class MaskedConfig:
    config: dict[str, Any]
    redacted_columns: list[str] = field(default_factory=list)
    redaction_reasons: dict[str, RedactionReason] = field(default_factory=dict)


def value_set_sensitivity(
    session: Session, suite: Suite
) -> Callable[[str, list[Any]], RedactionReason | None]:
    """The redaction ladder's own test for the TESTED column (``run_service._known_sensitive``):
    a governance tag (own or inherited through lineage), the suite's policy, fail-closed mode,
    or an affirmative name/value PII signal. A value set shown in a check is held to the same
    bar as a failing sample of that column. Answers which rung decided; ``None`` = not sensitive."""
    from backend.app.services.run_service import _known_sensitive_reason

    asset = session.get(Asset, suite.asset_id) if suite.asset_id else None
    tags = column_tags.effective_column_tags(session, asset)
    policy = suite.column_policy

    def reason(column: str, values: list[Any]) -> RedactionReason | None:
        return _known_sensitive_reason(column, values, policy, tags)

    return reason


class ValueSetMasker:
    """Masks configs for one read, resolving each suite's sensitivity test once."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tests: dict[uuid.UUID, Callable[[str, list[Any]], RedactionReason | None] | None] = {}

    def _test(
        self, suite_id: uuid.UUID
    ) -> Callable[[str, list[Any]], RedactionReason | None] | None:
        if suite_id not in self._tests:
            suite = self._session.get(Suite, suite_id)
            self._tests[suite_id] = (
                value_set_sensitivity(self._session, suite) if suite is not None else None
            )
        return self._tests[suite_id]

    def mask(self, suite_id: uuid.UUID, config: dict[str, Any] | None) -> MaskedConfig:
        config = dict(config or {})
        values = config.get(VALUE_SET_KEY)
        if not isinstance(values, list):
            return MaskedConfig(config)
        test = self._test(suite_id)
        column = str(config.get("column"))
        reason = test(column, values) if test is not None else None
        if reason is None:
            return MaskedConfig(config)
        config[VALUE_SET_KEY] = [REDACTED_MARKER for _ in values]
        return MaskedConfig(config, [column], {column: reason})
