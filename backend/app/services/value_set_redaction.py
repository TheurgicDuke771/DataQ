"""A check's value set over a column that is sensitive NOW is withheld on read (#2592).

A value set (`config.value_set`, on `expect_column_values_to_be_in_set` and every other type that
stores one) is the check's definition, but its literals are values of the tested column. Once that
column is classified as sensitive (the suite's policy, a warehouse tag, fail-closed mode, or an
affirmative PII signal) they are held to the bar a failing sample of it is: each value is replaced
by the redaction marker, the list keeps its length, and the read names the column and the rung
that decided.

The same holds for the other config keys that carry values of the column (#2682): pairs, patterns
(`regex`, `regex_list`, `like_pattern`, `like_pattern_list`), a set that is not a list, and the
bounds of a check whose bounds ARE values of the column — "values in range", the column's min or
max, and an aggregate monitor on `min` or `max`. Where bounds are counts or lengths (row count,
value lengths, an aggregate mean) they are not values of the column and are shown."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from backend.app.datasources.monitors import AGGREGATE_CELL_VALUES
from backend.app.db.models import Asset, Suite
from backend.app.services import column_tags
from backend.app.services.value_set_guard import BOUND_KEYS, REDACTED_MARKER, VALUE_KEYS

#: Check types whose `min_value` / `max_value` are values of the column, not counts.
_VALUE_BOUND_TYPES = frozenset(
    {
        "expect_column_values_to_be_between",
        # The column's own smallest / largest value, the same line the aggregate monitor's
        # `min` / `max` draws (#2240).
        "expect_column_min_to_be_between",
        "expect_column_max_to_be_between",
    }
)


def bound_keys_hold_values(expectation_type: str | None, config: Mapping[str, Any]) -> bool:
    """Whether this check's `min_value` / `max_value` are values of its column."""
    return expectation_type in _VALUE_BOUND_TYPES or config.get("aggregate") in (
        AGGREGATE_CELL_VALUES
    )


def _masked(value: Any) -> Any:
    """The marker in ``value``'s shape: a list keeps its length, a pair its two places."""
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return REDACTED_MARKER


def _leaves(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    return [value]


if TYPE_CHECKING:
    from backend.app.services.run_service import RedactionReason


@dataclass(frozen=True)
class MaskedConfig:
    config: dict[str, Any]
    redacted_columns: list[str] = field(default_factory=list)
    redaction_reasons: dict[str, RedactionReason] = field(default_factory=dict)


def policy_sensitivity(
    policy: Mapping[str, Any] | None, tags: Mapping[str, str] | None
) -> Callable[[str, list[Any]], RedactionReason | None]:
    """The redaction ladder's own test for the TESTED column (``run_service._known_sensitive``),
    over a column policy and the effective column tags: a governance tag, the policy,
    fail-closed mode, or an affirmative name/value PII signal. Answers which rung decided;
    ``None`` = not sensitive."""
    from backend.app.services.run_service import _known_sensitive_reason

    def reason(column: str, values: list[Any]) -> RedactionReason | None:
        return _known_sensitive_reason(column, values, policy, tags)

    return reason


def value_set_sensitivity(
    session: Session, suite: Suite
) -> Callable[[str, list[Any]], RedactionReason | None]:
    """`policy_sensitivity` for one suite: its column policy and its asset's tags (own or
    inherited through lineage). A value set shown in a check is held to the same bar as a
    failing sample of that column."""
    asset = session.get(Asset, suite.asset_id) if suite.asset_id else None
    return policy_sensitivity(
        suite.column_policy, column_tags.effective_column_tags(session, asset)
    )


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

    def mask(
        self,
        suite_id: uuid.UUID,
        config: dict[str, Any] | None,
        *,
        expectation_type: str | None = None,
    ) -> MaskedConfig:
        """``config`` with every value of a column that is sensitive now withheld.
        ``expectation_type`` decides whether the bounds are values (`bound_keys_hold_values`);
        without it they are shown."""
        return mask_check_values(config, self._test(suite_id), expectation_type=expectation_type)


def mask_check_values(
    config: Mapping[str, Any] | None,
    test: Callable[[str, list[Any]], RedactionReason | None] | None,
    *,
    expectation_type: str | None,
    column: str | None = None,
) -> MaskedConfig:
    """The masker's own rule over one config-shaped mapping and one sensitivity ``test``: a check's
    config on a read, or a result's ``expected_value``, which echoes the same kwargs (#2686).
    ``column`` stands in when the mapping names none."""
    config = dict(config or {})
    keys = [*VALUE_KEYS]
    if bound_keys_hold_values(expectation_type, config):
        keys += BOUND_KEYS
    present = [k for k in keys if config.get(k) not in (None, "", [], {})]
    if not present or test is None:
        return MaskedConfig(config)
    named = [
        config[k] for k in ("column", "column_A", "column_B") if isinstance(config.get(k), str)
    ]
    columns = named or [column if column is not None else str(config.get("column"))]
    reasons: dict[str, RedactionReason] = {}
    for key in present:
        values = _leaves(config[key])
        found = next(
            ((c, r) for c in columns if (r := reasons.get(c) or test(c, values)) is not None),
            None,
        )
        if found is None:
            continue
        reasons.setdefault(found[0], found[1])
        config[key] = _masked(config[key])
    if not reasons:
        return MaskedConfig(config)
    return MaskedConfig(config, list(reasons), reasons)


def redact_expected_value(
    expected: Any,
    *,
    tested_column: str | None,
    expectation_type: str | None,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
) -> Any:
    """A result's ``expected_value`` with the values of a sensitive column withheld (#2686).

    A GX result stores the check's kwargs here, so it carries the same value set, patterns and
    bounds a check read withholds; it is held to the same rule, against the same ladder its
    ``observed_value`` is (the policy and tags of the read, the check as of the result)."""
    if not isinstance(expected, dict):
        return expected
    return mask_check_values(
        expected,
        policy_sensitivity(policy, tags),
        expectation_type=expectation_type,
        column=tested_column,
    ).config
