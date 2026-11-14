"""Value literals in an ODCS document: withheld on export, refused on import (#2615).

A contract document lists values of its columns in a second place beside a check's
`config.value_set` (#2592): a property's `enum` and `examples`, the list arguments of a library
rule (`arguments.validValues`, `arguments.missingValues`, any sibling list; and the v3.0
`validValues` key), and a `type: custom` rule's `implementation` (`value_set`,
`value_pairs_set`, `regex_list` under a GX `kwargs` or a DataQ `config`). Once the column is
classified as sensitive they are held to the bar a check read is: each value becomes the
redaction marker, and the node says what was withheld, for which column and why.

Sensitivity is a fact about the column, not about one contract row: every suite linked to ANY
version of the contract for that schema object is asked, and so are the object's asset and every
suite on the same table. An object with none of those cannot be judged, so its lists are
withheld with their own reason (`SENSITIVITY_UNKNOWN`) — fail closed, and said so.

A document that still holds the marker in one of those places came from such an export, and
importing it would store the marker as the real values, so it is refused.

NOT covered (`NOT_WITHHELD`): patterns and bounds, SQL text, other engines' implementations,
custom property values. They are named to every reader rather than left to be assumed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import Session

from backend.app.contracts import translate as tr
from backend.app.contracts.targets import target_for
from backend.app.db.models import Asset, Connection, Contract, Suite
from backend.app.services import column_tags
from backend.app.services.asset_identity import resolve_asset_identity
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
#: On the document: the schema objects DataQ could not judge (no suite, no asset).
UNKNOWN_OBJECTS_PROPERTY = "sensitivityUnknownObjects"
#: The reason on a list withheld because its object's sensitivity could not be evaluated —
#: never one of the ladder's own (`policy`, `own_tag`, …), so "withheld because unknown" is
#: told apart from "withheld because classified".
SENSITIVITY_UNKNOWN: Final = "sensitivity_unknown"
#: What an export does NOT withhold, even over a sensitive column. Returned to MCP callers as a
#: field, and named in the guide: a blind spot stated only in prose is read as coverage.
NOT_WITHHELD: Final[tuple[str, ...]] = (
    "patterns: a rule's arguments.pattern, logicalTypeOptions.pattern, a custom rule's regex",
    "bounds: logicalTypeOptions.minimum / maximum and the like, and a rule's comparison operands",
    "SQL text: a sql rule's query, and custom SQL inside a custom rule",
    "the implementation of a custom rule whose engine is neither greatExpectations nor dataq",
    "customProperties values, descriptions, and any other free text",
)
_PREFIX = f"{tr.DATAQ_VENDOR}."
#: The keys of a custom rule's `kwargs` / `config` that hold values of the column.
_IMPLEMENTATION_VALUE_KEYS: Final = (VALUE_SET_KEY, "value_pairs_set", "regex_list")
#: The one list argument that names columns, not values (`duplicateValues`).
_COLUMN_LIST_ARGUMENT: Final = "properties"

_Sensitivity = Callable[[str, list[Any]], "str | None"]


def _leaves(value: Any) -> list[Any]:
    """Every scalar under ``value``: a list of pairs, or a mapping, hides values one level down."""
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    return [value]


def _masked(value: Any) -> Any:
    """The marker in ``value``'s shape: a list keeps its length (and a pair its two places)."""
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return REDACTED_MARKER


@dataclass
class _Slot:
    """One place in the document that holds values of a column: ``holder[key]``."""

    object: str | None
    #: The column the values belong to; None when the document does not say.
    column: str | None
    #: The property or quality rule the values sit on (where the withheld note is written).
    node: dict[str, Any]
    #: Their place on ``node``, as a reader would spell it (`arguments.validValues`).
    field: str
    holder: dict[str, Any]
    key: str
    #: Puts a parsed string `implementation` back on its rule; run only when the slot is masked.
    commit: Callable[[], None] | None = None
    #: Further columns the values belong to (`column_B` of a pair set).
    also: tuple[str, ...] = ()

    @property
    def is_enum(self) -> bool:
        return self.key == "enum"

    def values(self) -> list[Any]:
        raw = self.holder[self.key]
        if self.is_enum and isinstance(raw, list):
            return [v.get("value") if isinstance(v, dict) else v for v in raw]
        return _leaves(raw)

    def count(self) -> int:
        raw = self.holder[self.key]
        return len(raw) if isinstance(raw, list) else 1

    def mask(self) -> None:
        # `enum` entries must be unique, so the list collapses to one; the note counts it.
        self.holder[self.key] = (
            [{"value": REDACTED_MARKER}] if self.is_enum else _masked(self.holder[self.key])
        )


def _present(holder: dict[str, Any], key: str) -> bool:
    """A value is there to withhold: any shape, not only a list (a producer may write a bare
    string or a mapping where a list belongs)."""
    value = holder.get(key)
    return value is not None and value != [] and value != {} and value != ""


def _rule_slots(obj: str | None, column: str | None, rule: dict[str, Any]) -> Iterator[_Slot]:
    if _present(rule, "validValues"):  # the v3.0 spelling, kept beside `arguments`
        yield _Slot(obj, column, rule, "validValues", rule, "validValues")
    arguments = rule.get("arguments")
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            # Every list argument is a list of values (`validValues`, `missingValues`, and
            # whatever a producer spells beside them), bar the one that names columns.
            listed = isinstance(value, list) and key != _COLUMN_LIST_ARGUMENT
            if (listed or key in ("validValues", "missingValues")) and _present(arguments, key):
                yield _Slot(obj, column, rule, f"arguments.{key}", arguments, key)
    raw = rule.get("implementation")
    implementation = tr._parse_implementation(raw)
    if implementation is None:
        return

    def commit() -> None:
        rule["implementation"] = implementation

    for inner in ("kwargs", "config"):
        holder = implementation.get(inner)
        if not isinstance(holder, dict):
            continue
        named = [
            holder[k] for k in ("column", "column_A", "column_B") if isinstance(holder.get(k), str)
        ]
        for key in _IMPLEMENTATION_VALUE_KEYS:
            if _present(holder, key):
                yield _Slot(
                    obj,
                    named[0] if named else column,
                    rule,
                    f"implementation.{inner}.{key}",
                    holder,
                    key,
                    None if isinstance(raw, dict) else commit,
                    tuple(named[1:]),
                )


def _nested(prop: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every place ODCS v3 nests a property inside a property: an object's `properties`, an
    array's `items`, and a map's `map.key` / `map.value`."""
    for child in prop.get("properties") or ():
        if isinstance(child, dict):
            yield child
    if isinstance(prop.get("items"), dict):
        yield prop["items"]
    mapping = prop.get("map")
    if isinstance(mapping, dict):
        for side in ("key", "value"):
            if isinstance(mapping.get(side), dict):
                yield mapping[side]


def _property_slots(obj: str | None, column: str, prop: dict[str, Any]) -> Iterator[_Slot]:
    """A property's own lists, its rules', and those of everything nested under it — a nested
    field is part of the same column."""
    for key in ("enum", "examples"):
        if _present(prop, key):
            yield _Slot(obj, column, prop, key, prop, key)
    for rule in tr._rules_of(prop):
        yield from _rule_slots(obj, column, rule)
    for child in _nested(prop):
        yield from _property_slots(obj, column, child)


def _object_name(obj: dict[str, Any]) -> str | None:
    name = obj.get("name")
    return name if isinstance(name, str) else None


def _object_slots(obj: dict[str, Any]) -> Iterator[_Slot]:
    name = _object_name(obj)
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
                    "sensitive, or DataQ could not tell) — it is an export, not the contract; "
                    "import the producer's document, with the real values",
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
    """What the document itself says is PII — asked for every object, beside its suites."""
    columns = [
        column
        for prop in obj.get("properties") or ()
        if isinstance(prop, dict) and (column := tr.classification_pii(prop)) is not None
    ]
    return {"pii_columns": columns}


class _ContractSensitivity:
    """Who can say whether a column of one of the contract's schema objects is sensitive.

    Resolved per `(odcs_id, object)`, never per contract row: a suite re-pointed to a newer
    version, or the suites a newer version created, still speak for the same table."""

    def __init__(self, session: Session, contract: Contract) -> None:
        self._session = session
        versions = select(Contract.id).where(Contract.odcs_id == contract.odcs_id)
        #: Every suite linked to any version of this contract.
        self._linked = list(session.scalars(select(Suite).where(Suite.contract_id.in_(versions))))
        connection_ids = {s.connection_id for s in self._linked}
        #: The connections the contract was imported on — where its objects' tables live.
        self._connections = (
            list(session.scalars(select(Connection).where(Connection.id.in_(connection_ids))))
            if connection_ids
            else []
        )

    def _places(self, physical: str) -> tuple[list[tuple[uuid.UUID, dict[str, Any]]], set[Any]]:
        """Where the object's table is, with no suite to say: its run target on each of the
        contract's connections, and the asset identity each resolves to."""
        targets: list[tuple[uuid.UUID, dict[str, Any]]] = []
        identities: set[Any] = set()
        for connection in self._connections:
            target = target_for(connection.type, physical)
            if target is None:
                continue
            targets.append((connection.id, target))
            try:
                identity = resolve_asset_identity(connection.type, connection.config, target)
            except (ValueError, KeyError, TypeError):
                # A target this connection cannot place has no asset to ask. It still counts
                # in `targets`, and with no suite or asset the object is withheld as unknown.
                continue
            identities.add((identity.namespace, identity.name))
        return targets, identities

    def for_object(self, obj: dict[str, Any]) -> tuple[_Sensitivity, bool]:
        """`(test, known)` — the ladder over everything that can speak for the object, and
        whether anything could. ``known`` False = no suite and no asset: fail closed."""
        name = _object_name(obj)
        suites = {s.id: s for s in self._linked if name is not None and s.contract_object == name}
        targets, identities = self._places(tr._object_target(obj)) if name is not None else ([], [])
        asset_ids = {s.asset_id for s in suites.values() if s.asset_id is not None}
        assets = {
            a.id: a
            for a in (
                self._session.scalars(
                    select(Asset).where(
                        or_(
                            Asset.id.in_(asset_ids),
                            tuple_(Asset.namespace, Asset.name).in_(list(identities)),
                        )
                    )
                )
                if asset_ids or identities
                else ()
            )
        }
        # Every suite on the same table speaks too — one unlinked by hand, or never linked.
        same_table = [
            and_(Suite.connection_id == connection_id, Suite.target == target)
            for connection_id, target in targets
        ]
        if assets:
            same_table.append(Suite.asset_id.in_(list(assets)))
        if same_table:
            for suite in self._session.scalars(select(Suite).where(or_(*same_table))):
                suites.setdefault(suite.id, suite)

        policy = _document_policy(obj)
        tests = [value_set_sensitivity(self._session, suite) for suite in suites.values()]
        tests += [
            policy_sensitivity(policy, column_tags.effective_column_tags(self._session, asset))
            for asset in assets.values()
        ]
        tests.append(policy_sensitivity(policy, None))

        def reason(column: str, values: list[Any]) -> str | None:
            for test in tests:
                found = test(column, values)
                if found is not None:
                    return found
            return None

        return reason, bool(suites or assets)


def mask_document(
    session: Session,
    document: dict[str, Any],
    contract: Contract,
    *,
    withheld_upstream: Mapping[tuple[str | None, str | None], RedactionReason] | None = None,
) -> dict[str, str]:
    """Withhold, in place, every value list over a column that is sensitive now — or whose
    sensitivity nobody can state (`SENSITIVITY_UNKNOWN`). The caller's own visibility plays no
    part: a column is not less sensitive for a reader who cannot see the suite that says so.
    A list that already holds the marker (a suite-document check, masked by the suite export)
    is noted too, with the reason ``withheld_upstream`` gives for its `(object, column)`.
    Returns `{"<object>.<column>": reason}` (the bare `<object>` for a rule that names no
    column), also written on the document."""
    sensitivity = _ContractSensitivity(session, contract)
    for stale in (REDACTED_COLUMNS_PROPERTY, UNKNOWN_OBJECTS_PROPERTY):
        _drop_custom(document, stale)
    upstream = withheld_upstream or {}
    redacted: dict[str, str] = {}
    listed: list[dict[str, Any]] = []
    unknown: list[str | None] = []
    for obj in _objects(document):
        name = _object_name(obj)
        test, known = sensitivity.for_object(obj)
        if not known:
            unknown.append(name)
        notes: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        slots = list(_object_slots(obj))
        for slot in slots:
            _drop_custom(slot.node, WITHHELD_PROPERTY)
        for slot in slots:
            values = slot.values()
            already = REDACTED_MARKER in values
            clear = [v for v in values if v != REDACTED_MARKER]
            found: str | None = None
            for column in (slot.column if slot.column is not None else "", *slot.also):
                found = found or test(column, clear)
            if found is None and already:
                found = upstream.get((name, slot.column))
            if found is None and (already or not known):
                found = SENSITIVITY_UNKNOWN
            if found is None:
                continue
            count = slot.count()
            if slot.commit is not None:
                slot.commit()
            slot.mask()
            _, note = notes.setdefault(
                id(slot.node), (slot.node, {"column": slot.column, "reason": found, "fields": {}})
            )
            note["fields"][slot.field] = count
            label = ".".join(p for p in (name, slot.column) if p)
            if label not in redacted:
                redacted[label] = found
                listed.append({"object": name, "column": slot.column, "reason": found})
        for node, note in notes.values():
            _set_custom(node, WITHHELD_PROPERTY, note)
    if listed:
        _set_custom(document, REDACTED_COLUMNS_PROPERTY, listed)
    if unknown:
        _set_custom(document, UNKNOWN_OBJECTS_PROPERTY, unknown)
    return redacted


def redacted_columns(document: dict[str, Any]) -> dict[str, str]:
    """`{"<object>.<column>": reason}` an exported document says it withheld values for — the
    bare `<object>` when the rule names no column."""
    listed = tr.custom_properties(document).get(REDACTED_COLUMNS_PROPERTY)
    return {
        ".".join(str(p) for p in (item.get("object"), item.get("column")) if p): str(
            item.get("reason") or SENSITIVITY_UNKNOWN
        )
        for item in (listed if isinstance(listed, list) else ())
        if isinstance(item, dict)
    }


def sensitivity_unknown_objects(document: dict[str, Any]) -> list[str]:
    """The schema objects an exported document says DataQ could not judge."""
    listed = tr.custom_properties(document).get(UNKNOWN_OBJECTS_PROPERTY)
    return [str(name) for name in (listed if isinstance(listed, list) else ()) if name]
