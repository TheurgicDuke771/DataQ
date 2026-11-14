"""Value literals in an ODCS document: withheld on export, refused on import (#2615).

A contract document lists values of its columns in a second place beside a check's
`config.value_set` (#2592): a property's `enum` and `examples`, a library rule's
`arguments.validValues` / `arguments.missingValues` (and the v3.0 `validValues` key), and a
`type: custom` rule's `implementation` (a GX `kwargs.value_set`, a DataQ `config.value_set`).
Once the column is classified as sensitive they are held to the bar a check read is: each value
becomes the redaction marker, and the node says what was withheld, for which column and why.
A document that still holds the marker in one of those places came from such an export, and
importing it would store the marker as the real values, so it is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from backend.app.contracts import translate as tr
from backend.app.db.models import Suite
from backend.app.services.value_set_guard import (
    REDACTED_MARKER,
    VALUE_SET_KEY,
    ValueSetRedactedError,
)
from backend.app.services.value_set_redaction import policy_sensitivity, value_set_sensitivity

if TYPE_CHECKING:
    from backend.app.services.run_service import RedactionReason

#: On a property or quality rule whose values were withheld:
#: `{column, reason, fields: {<field>: <how many values>}}`.
WITHHELD_PROPERTY = "valuesWithheld"
#: On the document: `[{object, column, reason}]`, one per column with withheld values.
REDACTED_COLUMNS_PROPERTY = "redactedColumns"
_PREFIX = f"{tr.DATAQ_VENDOR}."

_Sensitivity = Callable[[str, list[Any]], "RedactionReason | None"]


@dataclass
class _Slot:
    """One list of column values in the document: ``holder[key]``."""

    object: str | None
    #: The column the values belong to; None when the document does not say.
    column: str | None
    #: The property or quality rule the list sits on (where the withheld note is written).
    node: dict[str, Any]
    #: The list's place on ``node``, as a reader would spell it (`arguments.validValues`).
    field: str
    holder: dict[str, Any]
    key: str
    #: Puts a parsed string `implementation` back on its rule; run only when the slot is masked.
    commit: Callable[[], None] | None = None

    @property
    def is_enum(self) -> bool:
        return self.key == "enum"

    def values(self) -> list[Any]:
        raw = self.holder[self.key]
        if self.is_enum:
            return [v.get("value") if isinstance(v, dict) else v for v in raw]
        return list(raw)


def _has_list(holder: dict[str, Any], key: str) -> bool:
    return isinstance(holder.get(key), list) and bool(holder[key])


def _rule_slots(obj: str | None, column: str | None, rule: dict[str, Any]) -> Iterator[_Slot]:
    if _has_list(rule, "validValues"):  # the v3.0 spelling, kept beside `arguments`
        yield _Slot(obj, column, rule, "validValues", rule, "validValues")
    arguments = rule.get("arguments")
    if isinstance(arguments, dict):
        for key in ("validValues", "missingValues"):
            if _has_list(arguments, key):
                yield _Slot(obj, column, rule, f"arguments.{key}", arguments, key)
    raw = rule.get("implementation")
    implementation = tr._parse_implementation(raw)
    if implementation is None:
        return

    def commit() -> None:
        rule["implementation"] = implementation

    for inner in ("kwargs", "config"):
        holder = implementation.get(inner)
        if isinstance(holder, dict) and _has_list(holder, VALUE_SET_KEY):
            named = holder.get("column")
            yield _Slot(
                obj,
                named if isinstance(named, str) else column,
                rule,
                f"implementation.{inner}.{VALUE_SET_KEY}",
                holder,
                VALUE_SET_KEY,
                None if isinstance(raw, dict) else commit,
            )


def _property_slots(obj: str | None, column: str, prop: dict[str, Any]) -> Iterator[_Slot]:
    """A property's own lists, its rules', and those of everything nested under it — a nested
    field is part of the same column."""
    for key in ("enum", "examples"):
        if _has_list(prop, key):
            yield _Slot(obj, column, prop, key, prop, key)
    for rule in tr._rules_of(prop):
        yield from _rule_slots(obj, column, rule)
    nested = [p for p in prop.get("properties") or () if isinstance(p, dict)]
    if isinstance(prop.get("items"), dict):
        nested.append(prop["items"])
    for child in nested:
        yield from _property_slots(obj, column, child)


def _object_slots(obj: dict[str, Any]) -> Iterator[_Slot]:
    name = obj.get("name") if isinstance(obj.get("name"), str) else None
    for rule in tr._rules_of(obj):
        yield from _rule_slots(name, None, rule)
    for prop in obj.get("properties") or ():
        if isinstance(prop, dict):
            yield from _property_slots(name, tr._column_name(prop), prop)


def _objects(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for obj in document.get("schema") or ():
        if isinstance(obj, dict):
            yield obj


def reject_masked_document(document: dict[str, Any]) -> None:
    """Refuse a document whose value lists still hold the redaction marker (an export with
    withheld values): storing it would replace the real values with the marker, and switch off
    the checks they back."""
    for obj in _objects(document):
        for slot in _object_slots(obj):
            if REDACTED_MARKER in slot.values():
                where = ".".join(str(p) for p in (slot.object, slot.column) if p)
                raise ValueSetRedactedError(
                    "this contract document holds withheld values (the column is classified as "
                    "sensitive) — it is an export, not the contract; import the producer's "
                    "document, with the real values",
                    detail={"object": slot.object, "column": slot.column, "field": slot.field}
                    | ({"where": where} if where else {}),
                )


def _drop_custom(node: dict[str, Any], name: str) -> None:
    key = f"{_PREFIX}{name}"
    props = node.get("customProperties")
    if not isinstance(props, list):
        return
    kept = [
        item
        for item in props
        if not (
            isinstance(item, dict)
            and (
                item.get("property") == key
                or (item.get("property") == name and item.get("vendor") == tr.DATAQ_VENDOR)
            )
        )
    ]
    if kept:
        node["customProperties"] = kept
    else:
        del node["customProperties"]


def _set_custom(node: dict[str, Any], name: str, value: Any) -> None:
    _drop_custom(node, name)
    node.setdefault("customProperties", []).append({"property": f"{_PREFIX}{name}", "value": value})


def _document_policy(obj: dict[str, Any]) -> dict[str, Any]:
    """What the document itself says is PII — the policy an object with no suite is held to."""
    columns = [
        column
        for prop in obj.get("properties") or ()
        if isinstance(prop, dict) and (column := tr.classification_pii(prop)) is not None
    ]
    return {"pii_columns": columns}


def _object_sensitivity(session: Session, obj: dict[str, Any], suites: list[Suite]) -> _Sensitivity:
    """The redaction ladder for one schema object: every suite linked to it is asked (its
    policy, its asset's tags), and the first rung that fires decides. An object no suite backs
    is held to the document's own classification and the PII classifier."""
    tests = [value_set_sensitivity(session, suite) for suite in suites]
    if not tests:
        tests = [policy_sensitivity(_document_policy(obj), None)]

    def reason(column: str, values: list[Any]) -> RedactionReason | None:
        for test in tests:
            found = test(column, values)
            if found is not None:
                return found
        return None

    return reason


def mask_document(
    session: Session,
    document: dict[str, Any],
    suites: list[Suite],
    *,
    withheld_upstream: Mapping[tuple[str | None, str | None], RedactionReason] | None = None,
) -> dict[str, RedactionReason | None]:
    """Withhold, in place, every value list over a column that is sensitive now. ``suites`` is
    every suite linked to the contract (not only the caller's visible ones — a column is not
    less sensitive for a reader who cannot see the suite that says so). A list that already
    holds the marker (a suite-document check, masked by the suite export) is noted too, with
    the reason ``withheld_upstream`` gives for its `(object, column)`. Returns
    `{"<object>.<column>": reason}`, also written on the document."""
    by_object: dict[str | None, list[Suite]] = {}
    for suite in suites:
        by_object.setdefault(suite.contract_object, []).append(suite)
    _drop_custom(document, REDACTED_COLUMNS_PROPERTY)
    upstream = withheld_upstream or {}
    redacted: dict[str, RedactionReason | None] = {}
    listed: list[dict[str, Any]] = []
    for obj in _objects(document):
        name = obj.get("name") if isinstance(obj.get("name"), str) else None
        test = _object_sensitivity(session, obj, by_object.get(name, []) if name else [])
        notes: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        slots = list(_object_slots(obj))
        for slot in slots:
            _drop_custom(slot.node, WITHHELD_PROPERTY)
        for slot in slots:
            values = slot.values()
            column = slot.column if slot.column is not None else ""
            already = REDACTED_MARKER in values
            found = test(column, [v for v in values if v != REDACTED_MARKER])
            if found is None and already:
                found = upstream.get((name, slot.column))
            if found is None and not already:
                continue
            if slot.commit is not None:
                slot.commit()
            # `enum` entries must be unique, so the list collapses to one; the note counts it.
            slot.holder[slot.key] = (
                [{"value": REDACTED_MARKER}] if slot.is_enum else [REDACTED_MARKER for _ in values]
            )
            _, note = notes.setdefault(
                id(slot.node), (slot.node, {"column": slot.column, "reason": found, "fields": {}})
            )
            note["fields"][slot.field] = len(values)
            label = ".".join(p for p in (name, slot.column) if p)
            if label not in redacted:
                redacted[label] = found
                listed.append({"object": name, "column": slot.column, "reason": found})
        for node, note in notes.values():
            _set_custom(node, WITHHELD_PROPERTY, note)
    if listed:
        _set_custom(document, REDACTED_COLUMNS_PROPERTY, listed)
    return redacted


def redacted_columns(document: dict[str, Any]) -> dict[str, RedactionReason | None]:
    """`{"<object>.<column>": reason}` an exported document says it withheld values for."""
    listed = tr.custom_properties(document).get(REDACTED_COLUMNS_PROPERTY)
    return {
        ".".join(str(p) for p in (item.get("object"), item.get("column")) if p): item.get("reason")
        for item in (listed if isinstance(listed, list) else ())
        if isinstance(item, dict)
    }
