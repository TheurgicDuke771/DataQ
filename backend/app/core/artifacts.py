"""Shared capped + logged JSON-artifact loader (ADR 0034, #759 review)."""

from __future__ import annotations

import json
from typing import Any

from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Refuse rather than attempt the load above this — real dbt artifacts reach tens of MB at thousands
# of models.
MAX_JSON_ARTIFACT_BYTES = 128 * 1024 * 1024


class ArtifactTooLargeError(Exception):
    """A JSON artifact exceeded the byte cap and was refused before parsing."""


def load_json_artifact(
    raw: bytes | bytearray, *, context: str, max_bytes: int = MAX_JSON_ARTIFACT_BYTES
) -> Any:
    """Parse ``raw`` JSON bytes, refusing (and logging) an oversized payload first."""
    if len(raw) > max_bytes:
        log.warning(
            "json_artifact_oversized", context=context, size_bytes=len(raw), cap_bytes=max_bytes
        )
        raise ArtifactTooLargeError(f"artifact is {len(raw)} bytes, above the {max_bytes}-byte cap")
    return json.loads(raw)
