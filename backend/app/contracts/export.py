"""A contract + its linked suites' current checks → an ODCS v3 document (ADR 0052 §4).

The stored spec is the base, so every field the producer wrote survives. DataQ-only facts ride in
`customProperties` with a `dataq.` prefix: on the document (`exportedAt`), on each schema object
backed by a suite (`suiteId`, `connectionType`), and on each quality rule a check enforces
(`checkId`, `kind`, `engine`, thresholds, `enabled`). A check with no ODCS-native shape — one a
person added to the suite, or any kind the table cannot express — is appended to its object as a
`type: custom, engine: dataq` rule whose `implementation` is the suite-document check, so
importing the export recreates every check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.contracts import odcs
from backend.app.contracts import translate as tr
from backend.app.db.models import (
    CHECK_ORDER,
    CONTRACT_CHECK_ORIGIN,
    Check,
    Connection,
    Contract,
    Suite,
)
from backend.app.services.suite_io_service import export_suite

_PREFIX = f"{tr.DATAQ_VENDOR}."


def _json_safe(value: Any) -> Any:
    """Thresholds are NUMERIC (`Decimal`); a YAML/JSON document wants plain numbers or strings."""
    return json.loads(json.dumps(value, default=lambda v: str(v) if isinstance(v, Decimal) else v))


def _set_custom(node: dict[str, Any], name: str, value: Any) -> None:
    """Replace any earlier `dataq.<name>` entry (an export of an export stays single-valued)."""
    key = f"{_PREFIX}{name}"

    def is_ours(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        prop = item.get("property")
        return prop == key or (prop == name and item.get("vendor") == tr.DATAQ_VENDOR)

    kept = [item for item in node.get("customProperties") or [] if not is_ours(item)]
    kept.append({"property": key, "value": _json_safe(value)})
    node["customProperties"] = kept


def _find_rule(obj: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any] | None:
    """The quality rule a check's clause names, by the same ids the importer recorded
    (`translate.rule_ids`). The clause names a property by its logical `name`."""
    prop_name = ref.get("property")
    container: dict[str, Any] | None
    if prop_name is None:
        container = obj
    else:
        container = next(
            (
                p
                for p in obj.get("properties") or ()
                if isinstance(p, dict) and str(p.get("name")) == prop_name
            ),
            None,
        )
        if container is None:
            return None
    rules = [r for r in container.get("quality") or () if isinstance(r, dict)]
    for rule, rule_id in zip(rules, tr.rule_ids(rules), strict=True):
        if rule_id == ref.get("rule_id"):
            return rule
    return None


def _annotate_rule(rule: dict[str, Any], check: Check) -> None:
    _set_custom(rule, "checkId", str(check.id))
    _set_custom(rule, "kind", check.kind)
    _set_custom(rule, "engine", check.engine)
    for name, value in (
        ("warnThreshold", check.warn_threshold),
        ("failThreshold", check.fail_threshold),
        ("criticalThreshold", check.critical_threshold),
    ):
        if value is not None:
            _set_custom(rule, name, value)
    _set_custom(rule, "enabled", check.enabled)


def _native_rule(check: Check, document: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "custom",
        "engine": tr.DATAQ_VENDOR,
        "name": check.name,
        "implementation": _json_safe(document),
        "customProperties": [{"property": f"{_PREFIX}checkId", "value": str(check.id)}],
    }


def export_contract(session: Session, contract: Contract, suites: list[Suite]) -> dict[str, Any]:
    """The ODCS document for ``contract`` as enforced by ``suites`` (the caller's visible ones,
    already authorised). Written in the newest vendored v3 vocabulary."""
    doc = tr.normalize_document(contract.spec)
    doc["apiVersion"] = odcs.NEWEST_VERSION
    _set_custom(doc, "exportedAt", datetime.now(UTC).isoformat())
    objects = {o.get("name"): o for o in doc.get("schema") or () if isinstance(o, dict)}
    # One query each for the connections and the checks of every suite, not one per suite.
    connections = {
        c.id: c
        for c in session.scalars(
            select(Connection).where(Connection.id.in_({s.connection_id for s in suites}))
        )
    }
    checks_by_suite: dict[Any, list[Check]] = {s.id: [] for s in suites}
    for check in session.scalars(
        select(Check).where(Check.suite_id.in_(list(checks_by_suite))).order_by(*CHECK_ORDER)
    ):
        checks_by_suite[check.suite_id].append(check)
    for suite in suites:
        obj = objects.get(suite.contract_object)
        if obj is None:
            continue
        connection = connections.get(suite.connection_id)
        _set_custom(obj, "suiteId", str(suite.id))
        if connection is not None:
            _set_custom(obj, "connectionType", connection.type)
        checks = checks_by_suite[suite.id]
        documents = export_suite(session, suite, checks=checks)["checks"]
        for check, document in zip(checks, documents, strict=True):
            ref = check.contract_ref if check.origin == CONTRACT_CHECK_ORIGIN else None
            if ref is not None:
                rule = _find_rule(obj, ref) if ref.get("rule_id") else None
                if rule is not None:
                    _annotate_rule(rule, check)
                # A schema-field clause (required, unique, …) is already in the document.
                if rule is not None or not ref.get("rule_id"):
                    continue
            obj.setdefault("quality", []).append(_native_rule(check, document))
    validated = odcs.validate_document(doc)
    return validated.document
