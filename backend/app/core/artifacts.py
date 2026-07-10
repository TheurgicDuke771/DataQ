"""Shared capped + logged JSON-artifact loader (ADR 0034, #759 review).

Both dbt-artifact read paths — the lineage `manifest.json` parse
(`lineage.dbt_manifest`) and the `run_results.json` poll (`orchestration.dbt`) —
`json.loads` bytes pulled from an untrusted store (ADLS / S3 / file). A hostile or
corrupt payload must not OOM the worker, so the load is refused above a byte cap
*before* `json.loads` ever runs, and an oversized artifact is logged (not silently
dropped). One helper so both paths share the guard and the ceiling.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Refuse rather than attempt the load above this — real dbt artifacts reach tens of
# MB at thousands of models; this ceiling is generous headroom (an ijson streaming
# path is the future upgrade for the manifest).
MAX_JSON_ARTIFACT_BYTES = 128 * 1024 * 1024


class ArtifactTooLargeError(Exception):
    """A JSON artifact exceeded the byte cap and was refused before parsing."""


def load_json_artifact(
    raw: bytes | bytearray, *, context: str, max_bytes: int = MAX_JSON_ARTIFACT_BYTES
) -> Any:
    """Parse ``raw`` JSON bytes, refusing (and logging) an oversized payload first.

    ``context`` is a human hint for the oversized-refusal log (e.g. the job name).
    Raises :class:`ArtifactTooLargeError` when ``raw`` is above ``max_bytes`` (the
    guard fires *before* ``json.loads`` so a huge/hostile payload is never parsed),
    or the usual ``json.loads`` errors (``ValueError`` / ``UnicodeDecodeError``) on
    malformed JSON — the caller decides whether to fail-soft or raise.
    """
    if len(raw) > max_bytes:
        log.warning(
            "json_artifact_oversized", context=context, size_bytes=len(raw), cap_bytes=max_bytes
        )
        raise ArtifactTooLargeError(f"artifact is {len(raw)} bytes, above the {max_bytes}-byte cap")
    return json.loads(raw)
