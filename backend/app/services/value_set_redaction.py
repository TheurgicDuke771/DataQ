"""A check's value set over a column that is sensitive NOW is withheld on read (#2592).

A value set (`config.value_set`, on `expect_column_values_to_be_in_set` and every other type that
stores one) is the check's definition, but its literals are values of the tested column. Once that
column is classified as sensitive (the suite's policy, a warehouse tag, fail-closed mode, or an
affirmative PII signal) they are held to the bar a failing sample of it is: each value is replaced
by the redaction marker, the list keeps its length, and the read names the column and the rung
that decided. Saving a config that still holds the marker is refused, so a round-trip edit can
never write the marker over the real values.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.db.models import Suite
from backend.app.services.run_service import _REDACTED_VALUE, RedactionReason

VALUE_SET_KEY = "value_set"
REDACTED_MARKER = _REDACTED_VALUE


class ValueSetRedactedError(DataQError):
    status_code = 422
    code = "value_set_redacted"


@dataclass(frozen=True)
class MaskedConfig:
    config: dict[str, Any]
    redacted_columns: list[str] = field(default_factory=list)
    redaction_reasons: dict[str, RedactionReason] = field(default_factory=dict)


class ValueSetMasker:
    """Masks configs for one read, resolving each suite's sensitivity test once."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tests: dict[uuid.UUID, Callable[[str, list[Any]], RedactionReason | None] | None] = {}

    def _test(
        self, suite_id: uuid.UUID
    ) -> Callable[[str, list[Any]], RedactionReason | None] | None:
        if suite_id not in self._tests:
            from backend.app.services.suggestion_service import value_set_sensitivity

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


def reject_masked_value_set(config: dict[str, Any] | None, *, check: str | None = None) -> None:
    """Refuse a config whose value set still holds the redaction marker: it came from a masked
    read, and saving it would replace the real values with the marker."""
    values = (config or {}).get(VALUE_SET_KEY)
    if isinstance(values, list) and REDACTED_MARKER in values:
        raise ValueSetRedactedError(
            "this value set holds withheld values (the column is classified as sensitive); "
            "send the full list of real values, or leave config out to keep the stored ones",
            detail={"field": f"config.{VALUE_SET_KEY}", **({"check": check} if check else {})},
        )
