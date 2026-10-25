"""ODCS v3 → DataQ check documents: the fixed mapping of ADR 0052 §3, one function per row.

Every schema field and every quality / SLA rule ends in exactly one place — a `CheckDraft`, or an
`Unmapped` entry with the reason — and nothing is dropped silently. The mapping is written in
v3.1+ vocabulary; `normalize_document` lifts a v3.0.x document into it first (translation only,
never the stored spec).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from dataclasses import field as _dc_field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from backend.app.datasources.base import FLAT_FILE_TYPES
from backend.app.datasources.expectation_allowlist import (
    UNBANDED_EXPECTATION_TYPES,
    is_allowed,
)
from backend.app.datasources.monitors import (
    FRESHNESS,
    SCHEMA_DRIFT,
    VOLUME,
    monitor_expectation_type,
)
from backend.app.db.models import CHECK_KINDS, DQ_DIMENSIONS, GX_ENGINE
from backend.app.services.custom_sql import (
    CUSTOM_SQL_EXPECTATION_TYPE,
    QUERY_KEY,
    SQL_QUERYABLE_TYPES,
    CustomSqlInvalidError,
    validate_query,
)
from backend.app.services.suite_document_yaml import (
    SuiteDocumentYamlInvalidError,
    parse_suite_document_yaml,
)

#: The `customProperties` vendor DataQ reads and writes (ADR 0052 §4).
DATAQ_VENDOR: Final = "dataq"
#: Datasources whose `schema_drift` introspection exists (`check_service.SCHEMA_DRIFT_CAPABLE_TYPES`
#: is the same set; named here to keep this module import-light).
_SCHEMA_DRIFT_TYPES: Final = frozenset({*SQL_QUERYABLE_TYPES, "iceberg", *FLAT_FILE_TYPES})

EXPECTATION_KIND: Final = "expectation"
#: ODCS `severity` → DataQ tier. Absent or unknown → `fail`: a contract clause is an assertion.
SEVERITY_TIERS: Final[dict[str, str]] = {
    "info": "warn",
    "warning": "warn",
    "error": "fail",
    "critical": "critical",
    "blocker": "critical",
}
DEFAULT_TIER: Final = "fail"
#: ODCS `dimension` → ADR 0038 vocabulary. `coverage` has no DataQ meaning → unclassified (NULL).
DIMENSIONS: Final[dict[str, str | None]] = {
    "accuracy": "accuracy",
    "completeness": "completeness",
    "conformity": "validity",
    "consistency": "consistency",
    "coverage": None,
    "timeliness": "timeliness",
    "uniqueness": "uniqueness",
}
#: Property `classification` values that put the column under the suite's redaction policy.
PII_CLASSIFICATIONS: Final = frozenset({"confidential", "restricted", "pii", "sensitive", "secret"})
#: SLA / latency time units (ISO-style) → hours. `m` is minutes.
UNIT_HOURS: Final[dict[str, Decimal]] = {
    "s": Decimal(1) / 3600,
    "sec": Decimal(1) / 3600,
    "second": Decimal(1) / 3600,
    "seconds": Decimal(1) / 3600,
    "m": Decimal(1) / 60,
    "min": Decimal(1) / 60,
    "minute": Decimal(1) / 60,
    "minutes": Decimal(1) / 60,
    "h": Decimal(1),
    "hr": Decimal(1),
    "hour": Decimal(1),
    "hours": Decimal(1),
    "d": Decimal(24),
    "day": Decimal(24),
    "days": Decimal(24),
    "w": Decimal(168),
    "wk": Decimal(168),
    "week": Decimal(168),
    "weeks": Decimal(168),
    "y": Decimal(8760),
    "yr": Decimal(8760),
    "year": Decimal(8760),
    "years": Decimal(8760),
}
GX_ENGINES: Final = frozenset({"great-expectations", "greatExpectations"})
#: ODCS comparison operators → SQL. Values are rendered by `_sql_literal`.
_SQL_OPERATORS: Final[dict[str, str]] = {
    "mustBe": "= {0}",
    "mustNotBe": "<> {0}",
    "mustBeGreaterThan": "> {0}",
    "mustBeGreaterOrEqualTo": ">= {0}",
    "mustBeLessThan": "< {0}",
    "mustBeLessOrEqualTo": "<= {0}",
    "mustBeBetween": "BETWEEN {0} AND {1}",
    "mustNotBeBetween": "NOT BETWEEN {0} AND {1}",
}
OPERATOR_KEYS: Final = frozenset(_SQL_OPERATORS)
#: v3.0.x deprecated `rule` names → v3.1 `metric`s (ADR 0052 §5).
_LEGACY_RULES: Final[dict[str, str]] = {
    "duplicateCount": "duplicateValues",
    "validValues": "invalidValues",
    "rowCount": "rowCount",
    "nullCount": "nullValues",
    "nullValues": "nullValues",
    "missingValues": "missingValues",
    "invalidValues": "invalidValues",
    "duplicateValues": "duplicateValues",
}
_PLACEHOLDERS: Final[tuple[tuple[str, str], ...]] = (
    ("${object}", "{object}"),
    ("${property}", "{property}"),
    ("${table}", "{object}"),
    ("${column}", "{property}"),
    ("{table}", "{object}"),
    ("{column}", "{property}"),
)
#: The band value meaning "any breach" on a row-wise expectation (its metric is the
#: unexpected-%): one bad row in a 10^11-row table is still above it.
ANY_UNEXPECTED_PERCENT: Final = Decimal("1e-9")
#: A custom-SQL check's metric is its unexpected ROW count, and the wrapped scalar comparison
#: yields exactly one row when it breaches.
ANY_UNEXPECTED_ROW: Final = Decimal(1)
_NAME_MAX: Final = 256
_PRIMARY_KEY_POSITION: Final = "primaryKeyPosition"


@dataclass(frozen=True)
class ClauseRef:
    """The contract clause a check enforces — `checks.contract_ref` (ADR 0052 §7)."""

    object: str
    field: str
    property: str | None = None
    rule_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "object": self.object,
            "property": self.property,
            "rule_id": self.rule_id,
            "field": self.field,
        }


@dataclass
class CheckDraft:
    """A check the importer will create, in `check_service.create_check`'s vocabulary."""

    name: str
    kind: str
    expectation_type: str
    config: dict[str, Any]
    ref: ClauseRef
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None
    dimension: str | None = None
    #: An explicit "leave unclassified" (ODCS `coverage`), which `dimension=None` cannot say.
    unclassified: bool = False
    engine: str = GX_ENGINE
    enabled: bool = True
    #: Declared `schema_drift` baseline columns (`type` None = not declared).
    baseline: list[dict[str, str | None]] | None = None
    #: A rule-level cron DataQ does not schedule, kept on the clause for the rollup.
    schedule: str | None = None
    #: `dataq.checkId` from an export — the existing check this draft stands for, if still there.
    adopt_check_id: str | None = None

    def fingerprint(self) -> str:
        """The rule's identity (type + config + thresholds), the `check_suggestions` idea."""
        return fingerprint(
            self.expectation_type,
            self.config,
            self.warn_threshold,
            self.fail_threshold,
            self.critical_threshold,
        )

    def ref_dict(self) -> dict[str, Any]:
        out = self.ref.as_dict()
        out["fingerprint"] = self.fingerprint()
        if self.schedule is not None:
            out["schedule"] = self.schedule
        return out


def fingerprint(
    expectation_type: str,
    config: dict[str, Any],
    warn: Decimal | None,
    fail: Decimal | None,
    critical: Decimal | None,
) -> str:
    canonical = json.dumps(
        {
            "type": expectation_type,
            "config": config,
            "thresholds": [None if t is None else str(Decimal(t)) for t in (warn, fail, critical)],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Unmapped:
    """A clause no check enforces, and why — a product fact, stored beside the spec."""

    object: str | None
    field: str
    reason: str
    property: str | None = None
    rule_id: str | None = None
    #: A small echo of the clause (metric name, SLA property, …) — never the whole rule.
    clause: dict[str, Any] = _dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "object": self.object,
            "property": self.property,
            "rule_id": self.rule_id,
            "field": self.field,
            "reason": self.reason,
            "clause": self.clause,
        }


@dataclass
class TranslatedObject:
    name: str
    physical_name: str
    checks: list[CheckDraft] = field(default_factory=list)
    pii_columns: list[str] = field(default_factory=list)
    schedule_cron: str | None = None
    schedule_timezone: str = "UTC"


@dataclass
class TranslatedContract:
    objects: list[TranslatedObject]
    unmapped: list[Unmapped]
    warnings: list[str] = field(default_factory=list)


# ───────────────────────── small helpers ─────────────────────────


def custom_properties(node: dict[str, Any], vendor: str = DATAQ_VENDOR) -> dict[str, Any]:
    """`{property: value}` of a node's `customProperties` for one vendor (unknown vendors kept
    by the caller, as the standard requires — this only reads). Both spellings are read: a
    `vendor: dataq` entry with a bare name (`schedule`), and a `dataq.`-prefixed name
    (`dataq.schedule`) — the form ADR 0052 writes, and the only one available before ODCS v3.2
    added `vendor`. The prefixed form wins when both are present."""
    prefix = f"{vendor}."
    bare: dict[str, Any] = {}
    prefixed: dict[str, Any] = {}
    for item in node.get("customProperties") or ():
        if not isinstance(item, dict):
            continue
        name = item.get("property")
        if not isinstance(name, str):
            continue
        if name.startswith(prefix):
            prefixed[name[len(prefix) :]] = item.get("value")
        elif item.get("vendor") == vendor:
            bare[name] = item.get("value")
    return {**bare, **prefixed}


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _operator(rule: dict[str, Any]) -> tuple[str, Any] | None:
    """The one comparison operator a rule carries, or None."""
    for key in _SQL_OPERATORS:
        if key in rule:
            return key, rule[key]
    return None


def _sql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _truncate(name: str) -> str:
    return name if len(name) <= _NAME_MAX else name[: _NAME_MAX - 1] + "…"


def _column_name(prop: dict[str, Any]) -> str:
    physical = prop.get("physicalName")
    return physical if isinstance(physical, str) and physical.strip() else str(prop.get("name"))


#: The parts of a quality rule that say WHAT it checks — not how strictly (operators, severity,
#: unit) or how it is labelled — so an id-less rule keeps its identity when a threshold changes or
#: a rule is inserted before it.
_RULE_IDENTITY_KEYS: Final = ("type", "metric", "query", "engine", "implementation", "arguments")


def rule_ids(rules: list[dict[str, Any]]) -> list[str]:
    """A stable clause id per rule: the rule's own `id`, else a content key over
    `_RULE_IDENTITY_KEYS` (`quality:<hash>`, with `#2`, `#3` … for identical siblings). Never a
    list position, which a reorder or insertion would silently rebind to another rule."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for rule in rules:
        rid = rule.get("id")
        if isinstance(rid, str) and rid:
            out.append(rid)
            continue
        identity = {k: rule.get(k) for k in _RULE_IDENTITY_KEYS if rule.get(k) is not None}
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        base = f"quality:{digest}"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return out


def _rule_label(rule: dict[str, Any], fallback: str) -> str:
    name = rule.get("name")
    return name if isinstance(name, str) and name.strip() else fallback


def _tier(rule: dict[str, Any]) -> str:
    override = custom_properties(rule).get("severity")
    if isinstance(override, str) and override in ("warn", "fail", "critical"):
        return override
    severity = rule.get("severity")
    if isinstance(severity, str):
        return SEVERITY_TIERS.get(severity.lower(), DEFAULT_TIER)
    return DEFAULT_TIER


def _dimension(rule: dict[str, Any]) -> tuple[str | None, bool]:
    """`(dimension, unclassified)` — absent → derive; `coverage` → explicitly unclassified."""
    value = rule.get("dimension")
    if not isinstance(value, str) or value not in DIMENSIONS:
        return None, False
    mapped = DIMENSIONS[value]
    return mapped, mapped is None


def _apply_tier(draft: CheckDraft, tier: str, band: Decimal) -> None:
    """Put `band` (the metric value meaning "breached") into the tier's threshold slot. A plain
    `fail` is the binary default and needs no threshold; an unbanded expectation can carry none.
    """
    if tier == DEFAULT_TIER or draft.expectation_type in UNBANDED_EXPECTATION_TYPES:
        return
    setattr(draft, f"{tier}_threshold", band)


def _apply_dataq_overrides(draft: CheckDraft, rule: dict[str, Any]) -> None:
    """`dataq.*` custom properties an export wrote (ADR 0052 §4): honoured when present."""
    props = custom_properties(rule)
    for prop, attr in (
        ("warnThreshold", "warn_threshold"),
        ("failThreshold", "fail_threshold"),
        ("criticalThreshold", "critical_threshold"),
    ):
        if prop in props:
            setattr(draft, attr, _decimal(props[prop]))
    if isinstance(props.get("enabled"), bool):
        draft.enabled = props["enabled"]
    check_id = props.get("checkId")
    if isinstance(check_id, str) and check_id:
        draft.adopt_check_id = check_id
    schedule = rule.get("schedule")
    if isinstance(schedule, str) and schedule.strip():
        draft.schedule = schedule.strip()


def _rule_draft(
    *,
    rule: dict[str, Any],
    ref: ClauseRef,
    name: str,
    expectation_type: str,
    config: dict[str, Any],
    band: Decimal,
    kind: str = EXPECTATION_KIND,
) -> CheckDraft:
    dimension, unclassified = _dimension(rule)
    draft = CheckDraft(
        name=_truncate(name),
        kind=kind,
        expectation_type=expectation_type,
        config=config,
        ref=ref,
        dimension=dimension,
        unclassified=unclassified,
    )
    _apply_tier(draft, _tier(rule), band)
    _apply_dataq_overrides(draft, rule)
    return draft


# ───────────────────────── v3.0.x normalisation ─────────────────────────


def normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    """A translation-time copy in v3.1+ vocabulary: deprecated `rule` names become `metric`s
    and `${…}` placeholders become `{…}`. The stored spec is never touched."""
    doc: dict[str, Any] = json.loads(json.dumps(document))
    for obj in doc.get("schema") or ():
        if not isinstance(obj, dict):
            continue
        for rule in _rules_of(obj):
            _normalize_rule(rule)
        for prop in obj.get("properties") or ():
            if isinstance(prop, dict):
                for rule in _rules_of(prop):
                    _normalize_rule(rule)
    return doc


def _rules_of(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for rule in node.get("quality") or ():
        if isinstance(rule, dict):
            yield rule


def _normalize_rule(rule: dict[str, Any]) -> None:
    if "metric" not in rule and isinstance(rule.get("rule"), str):
        legacy = rule["rule"]
        if legacy in _LEGACY_RULES:
            rule["metric"] = _LEGACY_RULES[legacy]
            if legacy == "validValues":
                arguments = dict(rule.get("arguments") or {})
                arguments.setdefault("validValues", rule.get("validValues"))
                rule["arguments"] = arguments
    query = rule.get("query")
    if isinstance(query, str):
        for old, new in _PLACEHOLDERS:
            query = query.replace(old, new)
        rule["query"] = query


# ───────────────────────── the table, one function per row ─────────────────────────


def required_check(obj: str, prop: dict[str, Any]) -> CheckDraft | None:
    """`required: true` → `expect_column_values_to_not_be_null`."""
    if prop.get("required") is not True:
        return None
    column = _column_name(prop)
    return CheckDraft(
        name=_truncate(f"{obj}.{column} required"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": column},
        ref=ClauseRef(object=obj, property=str(prop.get("name")), field="required"),
    )


def unique_check(obj: str, prop: dict[str, Any], *, primary_key_count: int) -> CheckDraft | None:
    """`unique: true`, or `primaryKey: true` when it is the only key column →
    `expect_column_values_to_be_unique`."""
    sole_key = prop.get("primaryKey") is True and primary_key_count == 1
    if prop.get("unique") is not True and not sole_key:
        return None
    column = _column_name(prop)
    return CheckDraft(
        name=_truncate(f"{obj}.{column} unique"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_values_to_be_unique",
        config={"column": column},
        ref=ClauseRef(
            object=obj,
            property=str(prop.get("name")),
            field="unique" if prop.get("unique") is True else "primaryKey",
        ),
    )


def compound_primary_key_check(obj: str, props: list[dict[str, Any]]) -> CheckDraft | None:
    """`primaryKey: true` on several properties → `expect_compound_columns_to_be_unique`."""
    keys = [p for p in props if p.get("primaryKey") is True]
    if len(keys) < 2:
        return None
    keys.sort(key=lambda p: (p.get(_PRIMARY_KEY_POSITION) is None, p.get(_PRIMARY_KEY_POSITION, 0)))
    columns = [_column_name(p) for p in keys]
    return CheckDraft(
        name=_truncate(f"{obj} primary key ({', '.join(columns)})"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_compound_columns_to_be_unique",
        config={"column_list": columns},
        ref=ClauseRef(object=obj, field="primaryKey"),
    )


def range_check(obj: str, prop: dict[str, Any]) -> tuple[CheckDraft | None, list[Unmapped]]:
    """`logicalTypeOptions.minimum/maximum` (+ exclusive forms → `strict_min`/`strict_max`) →
    `expect_column_values_to_be_between`; `multipleOf` → unmapped."""
    options = prop.get("logicalTypeOptions")
    if not isinstance(options, dict):
        return None, []
    column = _column_name(prop)
    unmapped: list[Unmapped] = []
    if "multipleOf" in options:
        unmapped.append(
            Unmapped(
                object=obj,
                property=str(prop.get("name")),
                field="logicalTypeOptions.multipleOf",
                reason="no DataQ check expresses a divisibility rule",
                clause={"multipleOf": options["multipleOf"]},
            )
        )
    config: dict[str, Any] = {"column": column}
    low = options.get("exclusiveMinimum", options.get("minimum"))
    high = options.get("exclusiveMaximum", options.get("maximum"))
    if _decimal(low) is None and _decimal(high) is None:
        return None, unmapped
    if _decimal(low) is not None:
        config["min_value"] = low
        if "exclusiveMinimum" in options:
            config["strict_min"] = True
    if _decimal(high) is not None:
        config["max_value"] = high
        if "exclusiveMaximum" in options:
            config["strict_max"] = True
    draft = CheckDraft(
        name=_truncate(f"{obj}.{column} range"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_values_to_be_between",
        config=config,
        ref=ClauseRef(object=obj, property=str(prop.get("name")), field="logicalTypeOptions.range"),
    )
    return draft, unmapped


def length_check(obj: str, prop: dict[str, Any]) -> CheckDraft | None:
    """`logicalTypeOptions.minLength/maxLength` → `expect_column_value_lengths_to_be_between`."""
    options = prop.get("logicalTypeOptions")
    if not isinstance(options, dict):
        return None
    low, high = options.get("minLength"), options.get("maxLength")
    if not isinstance(low, int) and not isinstance(high, int):
        return None
    column = _column_name(prop)
    config: dict[str, Any] = {"column": column}
    if isinstance(low, int) and not isinstance(low, bool):
        config["min_value"] = low
    if isinstance(high, int) and not isinstance(high, bool):
        config["max_value"] = high
    return CheckDraft(
        name=_truncate(f"{obj}.{column} length"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_value_lengths_to_be_between",
        config=config,
        ref=ClauseRef(
            object=obj, property=str(prop.get("name")), field="logicalTypeOptions.length"
        ),
    )


def pattern_check(obj: str, prop: dict[str, Any]) -> CheckDraft | None:
    """`logicalTypeOptions.pattern` → `expect_column_values_to_match_regex`."""
    options = prop.get("logicalTypeOptions")
    pattern = options.get("pattern") if isinstance(options, dict) else None
    if not isinstance(pattern, str) or not pattern:
        return None
    column = _column_name(prop)
    return CheckDraft(
        name=_truncate(f"{obj}.{column} pattern"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_values_to_match_regex",
        config={"column": column, "regex": pattern},
        ref=ClauseRef(
            object=obj, property=str(prop.get("name")), field="logicalTypeOptions.pattern"
        ),
    )


def enum_check(obj: str, prop: dict[str, Any]) -> CheckDraft | None:
    """`enum` (an array of `{value, …}` objects; bare scalars tolerated) →
    `expect_column_values_to_be_in_set`."""
    values = prop.get("enum")
    if not isinstance(values, list) or not values:
        return None
    value_set = [v.get("value") if isinstance(v, dict) else v for v in values]
    column = _column_name(prop)
    return CheckDraft(
        name=_truncate(f"{obj}.{column} allowed values"),
        kind=EXPECTATION_KIND,
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": column, "value_set": value_set},
        ref=ClauseRef(object=obj, property=str(prop.get("name")), field="enum"),
    )


def classification_pii(prop: dict[str, Any]) -> str | None:
    """`classification` ∈ PII_CLASSIFICATIONS → the column joins `column_policy.pii_columns`."""
    value = prop.get("classification")
    if isinstance(value, str) and value.strip().lower() in PII_CLASSIFICATIONS:
        return _column_name(prop)
    return None


def schema_drift_check(
    obj: str, props: list[dict[str, Any]], *, connection_type: str
) -> CheckDraft | Unmapped | None:
    """The property list → one `schema_drift` check with a DECLARED baseline (names + the
    `physicalType` when given; no type = never `type_changed` until a run records one)."""
    if not props:
        return None
    if connection_type not in _SCHEMA_DRIFT_TYPES:
        return Unmapped(
            object=obj,
            field="properties",
            reason=f"schema_drift is not supported on {connection_type!r} connections",
            clause={"columns": len(props)},
        )
    baseline: list[dict[str, str | None]] = [
        {
            "name": _column_name(p),
            "type": p["physicalType"] if isinstance(p.get("physicalType"), str) else None,
        }
        for p in props
    ]
    return CheckDraft(
        name=_truncate(f"{obj} schema"),
        kind=SCHEMA_DRIFT,
        expectation_type=monitor_expectation_type(SCHEMA_DRIFT),
        config={},
        ref=ClauseRef(object=obj, field="properties"),
        baseline=baseline,
    )


def _percent_band(rule: dict[str, Any]) -> tuple[Decimal | None, Unmapped | None]:
    """A `percent` operator bound → the threshold value; a `rows` bound → unmapped (not
    expressible as an unexpected-%); no operator → binary (zero tolerated)."""
    operator = _operator(rule)
    if operator is None:
        return None, None
    key, value = operator
    unit = str(rule.get("unit") or "rows").lower()
    bound = _decimal(value)
    if key in ("mustBe", "mustBeLessThan", "mustBeLessOrEqualTo") and bound == 0:
        return None, None  # "none at all" is the plain binary check
    if key == "mustBeLessThan" and unit == "rows" and bound == 1:
        return None, None
    if unit != "percent" or key not in ("mustBeLessThan", "mustBeLessOrEqualTo") or bound is None:
        return None, Unmapped(
            object=None,
            field="quality",
            reason=(
                "only a `percent` upper bound (mustBeLessThan / mustBeLessOrEqualTo) can band "
                "a row-wise check; a row count or another operator cannot"
            ),
            clause={key: value, "unit": unit},
        )
    return bound, None


def _with_rule_location(
    item: Unmapped, *, obj: str, prop: str | None, rule_id: str, extra: dict[str, Any]
) -> Unmapped:
    return Unmapped(
        object=obj,
        property=prop,
        rule_id=rule_id,
        field=item.field,
        reason=item.reason,
        clause={**extra, **item.clause},
    )


def library_rule(
    obj: str,
    rule: dict[str, Any],
    *,
    rule_id: str,
    prop: dict[str, Any] | None,
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """One `type: library` rule → checks, per metric (ADR 0052 §3 rows nullValues …
    rowCount). Object-level metrics other than `rowCount` / `duplicateValues` have no column."""
    metric = rule.get("metric")
    prop_name = str(prop.get("name")) if prop else None
    column = _column_name(prop) if prop else None
    where = f"{obj}.{column}" if column else obj
    ref = ClauseRef(object=obj, property=prop_name, rule_id=rule_id, field="quality")
    raw_arguments = rule.get("arguments")
    arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}

    def _unmapped(reason: str, **clause: Any) -> tuple[list[CheckDraft], list[Unmapped]]:
        return [], [
            Unmapped(
                object=obj,
                property=prop_name,
                rule_id=rule_id,
                field="quality",
                reason=reason,
                clause={"metric": metric, **clause},
            )
        ]

    if metric == "rowCount":
        return _row_count_rule(obj, rule, ref=ref, where=where)
    if metric == "duplicateValues":
        columns = arguments.get("properties")
        if column is not None:
            config: dict[str, Any] = {"column": column}
            etype = "expect_column_values_to_be_unique"
        elif isinstance(columns, list) and len(columns) == 1 and isinstance(columns[0], str):
            config, etype = {"column": columns[0]}, "expect_column_values_to_be_unique"
        elif isinstance(columns, list) and columns and all(isinstance(c, str) for c in columns):
            config, etype = {"column_list": list(columns)}, "expect_compound_columns_to_be_unique"
        else:
            return _unmapped("an object-level duplicateValues rule needs arguments.properties")
        band, problem = _percent_band(rule)
        drafts = [
            _rule_draft(
                rule=rule,
                ref=ref,
                name=f"{where} {_rule_label(rule, 'no duplicates')}",
                expectation_type=etype,
                config=config,
                band=band if band is not None else ANY_UNEXPECTED_PERCENT,
            )
        ]
        return _band_or_unmapped(drafts, band, problem, obj, prop_name, rule_id, metric)
    if column is None:
        return _unmapped(f"the {metric} metric needs a property; it was declared on the object")
    if metric == "nullValues":
        band, problem = _percent_band(rule)
        drafts = [
            _rule_draft(
                rule=rule,
                ref=ref,
                name=f"{where} {_rule_label(rule, 'nulls')}",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": column},
                band=band if band is not None else ANY_UNEXPECTED_PERCENT,
            )
        ]
        return _band_or_unmapped(drafts, band, problem, obj, prop_name, rule_id, metric)
    if metric == "missingValues":
        values = arguments.get("missingValues")
        if not isinstance(values, list) or not values:
            return _unmapped("missingValues needs arguments.missingValues (the values that count)")
        band, problem = _percent_band(rule)
        drafts = []
        listed = [v for v in values if v is not None]
        if listed:
            drafts.append(
                _rule_draft(
                    rule=rule,
                    ref=ref,
                    name=f"{where} {_rule_label(rule, 'no missing-value markers')}",
                    expectation_type="expect_column_values_to_not_be_in_set",
                    config={"column": column, "value_set": listed},
                    band=band if band is not None else ANY_UNEXPECTED_PERCENT,
                )
            )
        if len(listed) < len(values):
            drafts.append(
                _rule_draft(
                    rule=rule,
                    ref=ref,
                    name=f"{where} {_rule_label(rule, 'no missing-value markers')} (null)",
                    expectation_type="expect_column_values_to_not_be_null",
                    config={"column": column},
                    band=band if band is not None else ANY_UNEXPECTED_PERCENT,
                )
            )
        return _band_or_unmapped(drafts, band, problem, obj, prop_name, rule_id, metric)
    if metric == "invalidValues":
        valid = arguments.get("validValues")
        pattern = arguments.get("pattern")
        if isinstance(valid, list) and valid:
            etype, config = "expect_column_values_to_be_in_set", {
                "column": column,
                "value_set": valid,
            }
        elif isinstance(pattern, str) and pattern:
            etype, config = "expect_column_values_to_match_regex", {
                "column": column,
                "regex": pattern,
            }
        else:
            return _unmapped("invalidValues needs arguments.validValues or arguments.pattern")
        band, problem = _percent_band(rule)
        drafts = [
            _rule_draft(
                rule=rule,
                ref=ref,
                name=f"{where} {_rule_label(rule, 'valid values')}",
                expectation_type=etype,
                config=config,
                band=band if band is not None else ANY_UNEXPECTED_PERCENT,
            )
        ]
        return _band_or_unmapped(drafts, band, problem, obj, prop_name, rule_id, metric)
    return _unmapped("not an ODCS library metric DataQ knows")


def _band_or_unmapped(
    drafts: list[CheckDraft],
    band: Decimal | None,
    problem: Unmapped | None,
    obj: str,
    prop_name: str | None,
    rule_id: str,
    metric: Any,
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """A percent bound lands in the tier slot; an inexpressible bound makes the whole rule
    unmapped rather than a check that enforces something other than what the clause says."""
    if problem is not None:
        return [], [
            _with_rule_location(
                problem, obj=obj, prop=prop_name, rule_id=rule_id, extra={"metric": metric}
            )
        ]
    if band is not None:
        for draft in drafts:
            if draft.expectation_type not in UNBANDED_EXPECTATION_TYPES:
                tier = _tier_of(draft)
                draft.warn_threshold = draft.fail_threshold = draft.critical_threshold = None
                setattr(draft, f"{tier}_threshold", band)
    return drafts, []


def _tier_of(draft: CheckDraft) -> str:
    if draft.critical_threshold is not None:
        return "critical"
    if draft.warn_threshold is not None:
        return "warn"
    return "fail"


def _row_bounds(key: str, value: Any) -> dict[str, int] | None:
    """The whole-row band an operator allows — exact for a fractional bound (`> 5.7` is `>= 6`,
    `<= 5.4` is `<= 5`), `None` for an operator or value a row count cannot hold."""
    if key == "mustBeBetween":
        if not isinstance(value, list) or len(value) != 2:
            return None
        low, high = _decimal(value[0]), _decimal(value[1])
        if low is None or high is None or math.ceil(low) > math.floor(high):
            return None
        return {"min_rows": math.ceil(low), "max_rows": math.floor(high)}
    bound = _decimal(value)
    if bound is None:
        return None
    if key == "mustBe":
        return (
            {"min_rows": int(bound), "max_rows": int(bound)}
            if bound == bound.to_integral_value()
            else None
        )
    if key == "mustBeGreaterThan":
        return {"min_rows": math.floor(bound) + 1}
    if key == "mustBeGreaterOrEqualTo":
        return {"min_rows": math.ceil(bound)}
    if key == "mustBeLessThan":
        return {"max_rows": math.ceil(bound) - 1}
    if key == "mustBeLessOrEqualTo":
        return {"max_rows": math.floor(bound)}
    return None


def _row_count_rule(
    obj: str, rule: dict[str, Any], *, ref: ClauseRef, where: str
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """`rowCount` with an operator → a `volume` monitor; one-sided bounds stay one-sided."""
    operator = _operator(rule)
    metric_unmapped = Unmapped(
        object=obj, rule_id=ref.rule_id, field="quality", reason="", clause={"metric": "rowCount"}
    )
    if operator is None:
        return [], [
            Unmapped(
                **{**metric_unmapped.as_dict(), "reason": "rowCount needs a comparison operator"}
            )
        ]
    key, value = operator
    config = _row_bounds(key, value)
    if config is None:
        return [], [
            Unmapped(
                **{
                    **metric_unmapped.as_dict(),
                    "reason": f"{key} is not a row-count range a volume monitor can hold",
                    "clause": {"metric": "rowCount", key: value},
                }
            )
        ]
    draft = _rule_draft(
        rule=rule,
        ref=ref,
        name=f"{where} {_rule_label(rule, 'row count')}",
        kind=VOLUME,
        expectation_type=monitor_expectation_type(VOLUME),
        config=config,
        # The volume metric is the % outside the range: any excursion is > 0.
        band=ANY_UNEXPECTED_PERCENT,
    )
    return [draft], []


def sql_rule(
    obj: str,
    rule: dict[str, Any],
    *,
    rule_id: str,
    prop: dict[str, Any] | None,
    connection_type: str,
    object_target: str,
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """`type: sql` → a custom-SQL check (ADR 0019): `SELECT 1 WHERE NOT COALESCE((<query>)
    <operator>, FALSE)` — one unexpected row when the scalar breaches or is NULL."""
    prop_name = str(prop.get("name")) if prop else None
    column = _column_name(prop) if prop else None
    ref = ClauseRef(object=obj, property=prop_name, rule_id=rule_id, field="quality")

    def _unmapped(reason: str) -> tuple[list[CheckDraft], list[Unmapped]]:
        return [], [
            Unmapped(
                object=obj,
                property=prop_name,
                rule_id=rule_id,
                field="quality",
                reason=reason,
                clause={"type": "sql"},
            )
        ]

    if connection_type not in SQL_QUERYABLE_TYPES:
        return _unmapped(f"sql rules need a SQL datasource, not {connection_type!r}")
    query = rule.get("query")
    operator = _operator(rule)
    if not isinstance(query, str) or not query.strip():
        return _unmapped("sql rule has no query")
    if operator is None:
        return _unmapped("sql rule has no comparison operator for its scalar")
    key, value = operator
    if key in ("mustBeBetween", "mustNotBeBetween"):
        if not (isinstance(value, list) and len(value) == 2):
            return _unmapped(f"{key} needs a two-element list")
        comparison = _SQL_OPERATORS[key].format(_sql_literal(value[0]), _sql_literal(value[1]))
    else:
        comparison = _SQL_OPERATORS[key].format(_sql_literal(value))
    substituted = query.replace("{object}", object_target)
    if column is not None:
        substituted = substituted.replace("{property}", column)
    elif "{property}" in substituted:
        return _unmapped("the query names {property} but the rule is not on a property")
    wrapped = (
        f"SELECT 1 WHERE NOT COALESCE(({substituted.strip().rstrip(';')}) {comparison}, FALSE)"
    )
    try:
        validate_query(wrapped, connection_type=connection_type)
    except CustomSqlInvalidError as exc:
        return _unmapped(f"the query was refused by the custom-SQL validator: {exc.message}")
    where = f"{obj}.{column}" if column else obj
    draft = _rule_draft(
        rule=rule,
        ref=ref,
        name=f"{where} {_rule_label(rule, 'sql rule')}",
        expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
        config={QUERY_KEY: wrapped},
        band=ANY_UNEXPECTED_ROW,
    )
    return [draft], []


def _parse_implementation(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        try:
            parsed = parse_suite_document_yaml(raw)
        except SuiteDocumentYamlInvalidError:
            return None
    return parsed if isinstance(parsed, dict) else None


def custom_rule(
    obj: str,
    rule: dict[str, Any],
    *,
    rule_id: str,
    prop: dict[str, Any] | None,
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """`type: custom` — a GX `{expectation_type, kwargs}` or a DataQ check document; any other
    engine is not executable here."""
    prop_name = str(prop.get("name")) if prop else None
    column = _column_name(prop) if prop else None
    where = f"{obj}.{column}" if column else obj
    ref = ClauseRef(object=obj, property=prop_name, rule_id=rule_id, field="quality")
    engine = rule.get("engine")

    def _unmapped(reason: str) -> tuple[list[CheckDraft], list[Unmapped]]:
        return [], [
            Unmapped(
                object=obj,
                property=prop_name,
                rule_id=rule_id,
                field="quality",
                reason=reason,
                clause={"type": "custom", "engine": engine},
            )
        ]

    implementation = _parse_implementation(rule.get("implementation"))
    if engine in GX_ENGINES:
        if implementation is None:
            return _unmapped("implementation must be a {expectation_type, kwargs} mapping")
        etype = implementation.get("expectation_type") or implementation.get("type")
        kwargs = implementation.get("kwargs", {})
        if not isinstance(etype, str) or not isinstance(kwargs, dict):
            return _unmapped("implementation must be a {expectation_type, kwargs} mapping")
        if not is_allowed(etype):
            return _unmapped(f"{etype} is not a GX expectation DataQ authors")
        config = dict(kwargs)
        if column is not None:
            config.setdefault("column", column)
        draft = _rule_draft(
            rule=rule,
            ref=ref,
            name=f"{where} {_rule_label(rule, etype)}",
            expectation_type=etype,
            config=config,
            band=ANY_UNEXPECTED_PERCENT,
        )
        return [draft], []
    if engine == DATAQ_VENDOR:
        if implementation is None or not isinstance(implementation.get("expectation_type"), str):
            return _unmapped(
                "a dataq implementation is a DataQ check document (expectation_type …)"
            )
        kind = implementation.get("kind", EXPECTATION_KIND)
        if kind not in CHECK_KINDS:
            return _unmapped(f"unknown check kind {kind!r}")
        config = implementation.get("config", {})
        if not isinstance(config, dict):
            return _unmapped("config must be a mapping")
        dimension = implementation.get("dimension")
        draft = CheckDraft(
            name=_truncate(str(implementation.get("name") or f"{where} {rule_id}")),
            kind=str(kind),
            expectation_type=implementation["expectation_type"],
            config=config,
            ref=ref,
            warn_threshold=_decimal(implementation.get("warn_threshold")),
            fail_threshold=_decimal(implementation.get("fail_threshold")),
            critical_threshold=_decimal(implementation.get("critical_threshold")),
            dimension=dimension if dimension in DQ_DIMENSIONS else None,
            unclassified="dimension" in implementation and dimension is None,
            engine=str(implementation.get("engine", GX_ENGINE)),
            enabled=implementation.get("enabled", True) is not False,
        )
        _apply_dataq_overrides(draft, rule)
        return [draft], []
    return _unmapped(f"engine {engine!r} is not executable by DataQ")


def text_rule(
    obj: str, rule: dict[str, Any], *, rule_id: str, prop: dict[str, Any] | None
) -> Unmapped:
    """`type: text` is prose → unmapped."""
    return Unmapped(
        object=obj,
        property=str(prop.get("name")) if prop else None,
        rule_id=rule_id,
        field="quality",
        reason="a text rule is prose, not an executable check",
        clause={"type": "text", "name": rule.get("name")},
    )


def quality_rule(
    obj: str,
    rule: dict[str, Any],
    *,
    rule_id: str,
    prop: dict[str, Any] | None,
    connection_type: str,
    object_target: str,
) -> tuple[list[CheckDraft], list[Unmapped]]:
    """Dispatch one quality rule by `type` (default `library`); a rule-level `schedule` is
    recorded as unmapped beside whatever the rule produced (DataQ schedules a suite)."""
    rule_type = rule.get("type") or "library"
    if rule_type == "library":
        drafts, unmapped = library_rule(obj, rule, rule_id=rule_id, prop=prop)
    elif rule_type == "sql":
        drafts, unmapped = sql_rule(
            obj,
            rule,
            rule_id=rule_id,
            prop=prop,
            connection_type=connection_type,
            object_target=object_target,
        )
    elif rule_type == "custom":
        drafts, unmapped = custom_rule(obj, rule, rule_id=rule_id, prop=prop)
    else:
        drafts, unmapped = [], [text_rule(obj, rule, rule_id=rule_id, prop=prop)]
    schedule = rule.get("schedule")
    if isinstance(schedule, str) and schedule.strip():
        unmapped.append(
            Unmapped(
                object=obj,
                property=str(prop.get("name")) if prop else None,
                rule_id=rule_id,
                field="schedule",
                reason="DataQ schedules a suite, not a rule; the cron is kept on the clause",
                clause={"schedule": schedule.strip(), "scheduler": rule.get("scheduler")},
            )
        )
    return drafts, unmapped


def _hours(value: Any, unit: Any) -> Decimal | None:
    amount = _decimal(value)
    factor = UNIT_HOURS.get(str(unit).strip().lower()) if unit is not None else None
    if amount is None or factor is None or amount <= 0:
        return None
    return amount * factor


def freshness_check(
    obj: str,
    *,
    column: str | None,
    hours: Decimal,
    ref: ClauseRef,
    connection_type: str,
    label: str,
    tier: str = DEFAULT_TIER,
) -> CheckDraft | Unmapped:
    """A staleness ceiling → a `freshness` monitor on `column`, or arrival-time freshness on a
    flat-file datasource when no column is named. The kind needs a fail/critical threshold, so
    a `warn` tier is promoted to `fail`."""
    if column is None and connection_type not in FLAT_FILE_TYPES:
        return Unmapped(
            object=obj,
            rule_id=ref.rule_id,
            field=ref.field,
            reason=(
                f"a freshness ceiling on {connection_type!r} needs an element naming a timestamp "
                "column; only flat-file datasources measure arrival time"
            ),
            clause={"hours": str(hours)},
        )
    draft = CheckDraft(
        name=_truncate(f"{obj}{'.' + column if column else ''} {label}"),
        kind=FRESHNESS,
        expectation_type=monitor_expectation_type(FRESHNESS),
        config={"column": column} if column is not None else {},
        ref=ref,
    )
    if tier == "critical":
        draft.critical_threshold = hours
    else:
        draft.fail_threshold = hours
    return draft


def _split_element(element: Any) -> tuple[str | None, str | None]:
    """`object.property` → (object, property); `object` → (object, None)."""
    if not isinstance(element, str) or not element.strip():
        return None, None
    head, sep, tail = element.strip().partition(".")
    return head, (tail or None) if sep else None


def sla_property(
    sla: dict[str, Any],
    *,
    index: int,
    default_element: Any,
    objects: dict[str, TranslatedObject],
    connection_type: str,
) -> Unmapped | None:
    """`latency` with a time unit → freshness on the element's object; `frequency` and every
    other SLA property → unmapped. Returns the unmapped entry, or None when a check was added."""
    prop = str(sla.get("property") or "")
    sla_id = sla.get("id") if isinstance(sla.get("id"), str) else f"slaProperties[{index}]"
    obj_name, column = _split_element(sla.get("element", default_element))
    if prop == "frequency":
        return Unmapped(
            object=obj_name,
            rule_id=sla_id,
            field="slaProperties.frequency",
            reason=(
                "a refresh cadence is not a staleness ceiling — a daily feed checked at 24h "
                "flaps on every run; state the ceiling as latency or dataq.maxAgeHours"
            ),
            clause={"value": sla.get("value"), "unit": sla.get("unit")},
        )
    if prop != "latency":
        return Unmapped(
            object=obj_name,
            rule_id=sla_id,
            field=f"slaProperties.{prop or '?'}",
            reason="not a data-quality check",
            clause={"value": sla.get("value"), "unit": sla.get("unit")},
        )
    hours = _hours(sla.get("value"), sla.get("unit"))
    if hours is None:
        return Unmapped(
            object=obj_name,
            rule_id=sla_id,
            field="slaProperties.latency",
            reason="latency needs a positive value and a time unit (s/m/h/d/w/y)",
            clause={"value": sla.get("value"), "unit": sla.get("unit")},
        )
    target = objects.get(obj_name or "")
    if target is None:
        return Unmapped(
            object=obj_name,
            rule_id=sla_id,
            field="slaProperties.latency",
            reason="the element names no schema object of this contract",
            clause={"element": sla.get("element", default_element)},
        )
    result = freshness_check(
        target.name,
        column=column,
        hours=hours,
        ref=ClauseRef(object=target.name, rule_id=sla_id, field="slaProperties.latency"),
        connection_type=connection_type,
        label="latency",
        tier=_tier(sla),
    )
    if isinstance(result, Unmapped):
        return result
    target.checks.append(result)
    return None


def max_age_property(
    target: TranslatedObject, props: dict[str, Any], *, connection_type: str
) -> Unmapped | None:
    """`customProperties[dataq.maxAgeHours]` (+ optional `dataq.freshnessColumn`) on an object
    → the same freshness monitor as `latency`."""
    if "maxAgeHours" not in props:
        return None
    hours = _decimal(props["maxAgeHours"])
    ref = ClauseRef(object=target.name, field="customProperties.dataq.maxAgeHours")
    if hours is None or hours <= 0:
        return Unmapped(
            object=target.name,
            field=ref.field,
            reason="dataq.maxAgeHours must be a positive number of hours",
            clause={"maxAgeHours": props["maxAgeHours"]},
        )
    column = props.get("freshnessColumn")
    result = freshness_check(
        target.name,
        column=column if isinstance(column, str) and column else None,
        hours=hours,
        ref=ref,
        connection_type=connection_type,
        label="max age",
    )
    if isinstance(result, Unmapped):
        return result
    target.checks.append(result)
    return None


# ───────────────────────── the whole document ─────────────────────────


def _object_target(obj: dict[str, Any]) -> str:
    physical = obj.get("physicalName")
    return physical if isinstance(physical, str) and physical.strip() else str(obj["name"])


def translate(
    document: dict[str, Any],
    *,
    connection_type: str,
    object_target: Callable[[str], str] | None = None,
) -> TranslatedContract:
    """The ODCS document → per-object check drafts + the unmapped list. `object_target` renders
    an object's physical name as the datasource addresses it (for `{object}` in sql rules);
    the physical name itself by default."""
    doc = normalize_document(document)
    objects: dict[str, TranslatedObject] = {}
    unmapped: list[Unmapped] = []
    warnings: list[str] = []
    for obj in doc.get("schema") or ():
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
            continue
        name = obj["name"]
        if name in objects:
            warnings.append(f"schema object {name!r} appears more than once; the first one wins")
            continue
        physical = _object_target(obj)
        target = TranslatedObject(name=name, physical_name=physical)
        objects[name] = target
        rendered = object_target(physical) if object_target else physical
        props = [p for p in obj.get("properties") or () if isinstance(p, dict)]
        key_count = sum(1 for p in props if p.get("primaryKey") is True)
        for prop in props:
            if not isinstance(prop.get("name"), str):
                continue
            for draft in (
                required_check(name, prop),
                unique_check(name, prop, primary_key_count=key_count),
                length_check(name, prop),
                pattern_check(name, prop),
                enum_check(name, prop),
            ):
                if draft is not None:
                    target.checks.append(draft)
            ranged, more = range_check(name, prop)
            if ranged is not None:
                target.checks.append(ranged)
            unmapped.extend(more)
            if (pii := classification_pii(prop)) is not None:
                target.pii_columns.append(pii)
            prop_rules = list(_rules_of(prop))
            for rule, rule_id in zip(prop_rules, rule_ids(prop_rules), strict=True):
                drafts, missed = quality_rule(
                    name,
                    rule,
                    rule_id=rule_id,
                    prop=prop,
                    connection_type=connection_type,
                    object_target=rendered,
                )
                target.checks.extend(drafts)
                unmapped.extend(missed)
        if (compound := compound_primary_key_check(name, props)) is not None:
            target.checks.append(compound)
        drift = schema_drift_check(name, props, connection_type=connection_type)
        if isinstance(drift, CheckDraft):
            target.checks.append(drift)
        elif drift is not None:
            unmapped.append(drift)
        obj_rules = list(_rules_of(obj))
        for rule, rule_id in zip(obj_rules, rule_ids(obj_rules), strict=True):
            drafts, missed = quality_rule(
                name,
                rule,
                rule_id=rule_id,
                prop=None,
                connection_type=connection_type,
                object_target=rendered,
            )
            target.checks.extend(drafts)
            unmapped.extend(missed)
        dataq_props = custom_properties(obj)
        if isinstance(dataq_props.get("schedule"), str) and dataq_props["schedule"].strip():
            target.schedule_cron = dataq_props["schedule"].strip()
            tz = dataq_props.get("timezone")
            if isinstance(tz, str) and tz.strip():
                target.schedule_timezone = tz.strip()
        if missed_age := max_age_property(target, dataq_props, connection_type=connection_type):
            unmapped.append(missed_age)
    for index, sla in enumerate(doc.get("slaProperties") or ()):
        if not isinstance(sla, dict):
            continue
        missed_sla = sla_property(
            sla,
            index=index,
            default_element=doc.get("slaDefaultElement"),
            objects=objects,
            connection_type=connection_type,
        )
        if missed_sla is not None:
            unmapped.append(missed_sla)
    return TranslatedContract(objects=list(objects.values()), unmapped=unmapped, warnings=warnings)
