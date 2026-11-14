"""An ODCS schema object's `physicalName` → a datasource run target.

A leaf module: the importer builds a suite's target with it, and the export's redaction uses it
to find the object's asset without a suite.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.run_target import SuiteTargetInvalidError, validate_target


def target_for(connection_type: str, physical: str) -> dict[str, Any] | None:
    """Map an ODCS `physicalName` (`table`, `schema.table`, `catalog.schema.table`, or a file
    path) onto this datasource's run-target shape, or `None` when it does not resolve."""
    parts = [p for p in physical.split(".") if p] if "/" not in physical else []
    target: dict[str, Any]
    if connection_type in {"adls_gen2", "s3", "gcs"}:
        target = {"path": physical}
    elif connection_type == "unity_catalog":
        if len(parts) != 3:
            return None
        target = {"catalog": parts[0], "schema": parts[1], "table": parts[2]}
    elif connection_type == "iceberg":
        if not parts:
            return None
        target = {"table": parts[-1]}
        if len(parts) > 1:
            target["namespace"] = ".".join(parts[:-1])
    else:
        if not parts or len(parts) > 2:
            return None
        target = {"table": parts[-1]}
        if len(parts) == 2:
            target["schema"] = parts[0]
    try:
        validate_target(connection_type, target)
    except SuiteTargetInvalidError:
        return None
    return target
