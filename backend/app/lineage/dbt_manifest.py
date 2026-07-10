"""Parse a dbt ``manifest.json`` into a table-level dependency graph (ADR 0034).

Slice 2 of the lineage plan (#759): the sibling of ``run_results.json`` the
ADR-0029 dbt provider already polls, ``manifest.json``, carries the model
dependency graph. This module turns its **minimal stable subset**
(``metadata`` + ``nodes`` + ``sources`` + ``parent_map``) into a
:class:`ManifestGraph` — node identities (``database``/``schema``/name) and
TABLE-level ``(parent_uid, child_uid)`` edges — for the mapping layer
(``lineage.edges``) to canonicalize into OpenLineage asset names and cache.

Pure: no DB, no IO, no network — bytes in, a frozen dataclass out. It **never**
reads ``compiled_code`` / ``raw_code`` / ``description`` / ``docs`` (large,
churny, and irrelevant to lineage), and refuses an oversized payload rather than
attempt the ``json.loads`` (an ijson streaming path is the future optimization —
real manifests reach ~75 MB at thousands of models; the harness's is ~600 KB).

Defensive by contract: any malformed input — truncated bytes, NUL bytes, a
non-dict payload, a missing ``metadata`` / ``parent_map`` / ``nodes`` /
``sources`` key, an unparsable or too-old ``dbt_schema_version`` — raises
:class:`ManifestParseError`, never a bare ``KeyError`` / ``UnicodeDecodeError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.app.core.artifacts import (
    MAX_JSON_ARTIFACT_BYTES,
    ArtifactTooLargeError,
    load_json_artifact,
)
from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Refuse rather than attempt the load above this — a hostile/corrupt payload must
# not OOM the worker. Real manifests hit tens of MB at thousands of models; this
# ceiling is generous headroom (an ijson streaming path is the future upgrade).
# Shared with `run_results.json` via `core.artifacts` (one cap for both dbt reads).
_MAX_MANIFEST_BYTES = MAX_JSON_ARTIFACT_BYTES

# `dbt_schema_version` looks like ".../dbt/manifest/v12.json" — but variants drop
# the `.json` or carry a query suffix (`/v12`, `/v12.json?x=1`). Grab the NN after
# `/v`, tolerating anything after it.
_SCHEMA_VERSION_RE = re.compile(r"/v(\d+)")
# v12 is stable across dbt-core 1.8->1.11 (ADR 0034). v10-v11 parse best-effort with
# a warning; anything below v10 (or unparsable) is refused.
_TARGET_SCHEMA_VERSION = 12
_MIN_SCHEMA_VERSION = 10

# `nodes` resource types that are physical tables/views we track. Everything else
# in `nodes` (tests, operations, analyses) is dropped; all `sources` are included.
_NODE_RESOURCE_TYPES = frozenset({"model", "seed", "snapshot"})


class ManifestParseError(Exception):
    """A dbt manifest could not be safely parsed into a graph."""


@dataclass(frozen=True)
class NodeIdentity:
    """A physical node's warehouse identity — ``database`` / ``schema`` / name.

    ``name`` is the node's ``alias`` (models/seeds/snapshots) or the source's
    ``name``; the mapping layer canonicalizes ``(database, schema, name)`` into an
    OpenLineage asset name per the connection's adapter.
    """

    database: str
    schema: str
    name: str


@dataclass(frozen=True)
class ManifestGraph:
    """A parsed manifest: adapter, physical node identities, and TABLE-level edges.

    ``nodes`` maps each physical node's ``unique_id`` to its :class:`NodeIdentity`
    (ephemeral nodes are collapsed out). ``edges`` are ``(parent_uid, child_uid)``
    pairs over those physical nodes — the dependency graph with tests/operations
    and ephemeral hops removed.
    """

    adapter_type: str
    nodes: dict[str, NodeIdentity]
    edges: list[tuple[str, str]]


def parse_manifest(raw: bytes) -> ManifestGraph:
    """Parse ``raw`` manifest bytes into a :class:`ManifestGraph`.

    Raises :class:`ManifestParseError` on any malformed / oversized / too-old
    input — never a bare ``KeyError`` / ``UnicodeDecodeError``.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ManifestParseError("manifest payload must be bytes")
    try:
        # Shared capped loader — refuses (log-visible) an oversized payload before
        # `json.loads`, so a hostile/corrupt manifest never OOMs the worker.
        doc = load_json_artifact(raw, context="dbt manifest", max_bytes=_MAX_MANIFEST_BYTES)
    except ArtifactTooLargeError as exc:
        raise ManifestParseError(str(exc)) from exc
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ManifestParseError("manifest is not valid JSON") from exc
    if not isinstance(doc, dict):
        raise ManifestParseError("manifest root must be a JSON object")

    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        raise ManifestParseError("manifest missing 'metadata' object")
    _check_schema_version(metadata)
    adapter_type = _as_str(metadata.get("adapter_type"))

    nodes_raw = doc.get("nodes")
    sources_raw = doc.get("sources")
    parent_map_raw = doc.get("parent_map")
    if (
        not isinstance(nodes_raw, dict)
        or not isinstance(sources_raw, dict)
        or not isinstance(parent_map_raw, dict)
    ):
        raise ManifestParseError("manifest missing 'nodes' / 'sources' / 'parent_map' objects")

    identities: dict[str, NodeIdentity] = {}
    physical: set[str] = set()
    ephemeral: set[str] = set()
    # Every read below is a `.get()` on an `isinstance`-guarded dict (`_identity` /
    # `_is_ephemeral` never index or attribute-access), so no per-node try/except is
    # needed — a malformed node is skipped by the `isinstance` guard, not caught.
    for uid, node in sources_raw.items():
        if not isinstance(node, dict):
            continue
        identities[uid] = _identity(node, name_key="name")
        physical.add(uid)
    for uid, node in nodes_raw.items():
        if not isinstance(node, dict) or node.get("resource_type") not in _NODE_RESOURCE_TYPES:
            continue
        identities[uid] = _identity(node, name_key="alias")
        (ephemeral if _is_ephemeral(node) else physical).add(uid)

    # `parent_map` is pre-flattened (dbt's own parent index) — the edge source of
    # record (never re-derive from depends_on). Keep only list-valued entries.
    parent_map = {k: v for k, v in parent_map_raw.items() if isinstance(v, list)}
    edges = _build_edges(parent_map, physical, ephemeral)
    nodes = {uid: identities[uid] for uid in physical}
    return ManifestGraph(adapter_type=adapter_type, nodes=nodes, edges=edges)


def _check_schema_version(metadata: dict[str, Any]) -> None:
    raw = metadata.get("dbt_schema_version")
    match = _SCHEMA_VERSION_RE.search(raw) if isinstance(raw, str) else None
    if match is None:
        raise ManifestParseError(f"unrecognised dbt_schema_version: {raw!r}")
    version = int(match.group(1))
    if version < _MIN_SCHEMA_VERSION:
        raise ManifestParseError(
            f"dbt_schema_version v{version} is below the minimum supported "
            f"v{_MIN_SCHEMA_VERSION}"
        )
    if version != _TARGET_SCHEMA_VERSION:
        # v10/v11 (and any future >v12) parse best-effort — the minimal subset we
        # read is stable, but flag the drift so a real break is diagnosable.
        log.warning(
            "dbt_manifest_schema_version_drift",
            version=version,
            target=_TARGET_SCHEMA_VERSION,
        )


def _identity(node: dict[str, Any], *, name_key: str) -> NodeIdentity:
    name = _as_str(node.get(name_key)) or _as_str(node.get("name"))
    return NodeIdentity(
        database=_as_str(node.get("database")),
        schema=_as_str(node.get("schema")),
        name=name,
    )


def _is_ephemeral(node: dict[str, Any]) -> bool:
    """An ephemeral model is not a physical table — collapse it out of the graph.

    dbt marks it ``config.materialized == "ephemeral"``; belt-and-braces, a node
    with no ``relation_name`` also has no physical relation to key an asset on.
    """
    config = node.get("config")
    if isinstance(config, dict) and config.get("materialized") == "ephemeral":
        return True
    return node.get("relation_name") is None


def _build_edges(
    parent_map: dict[str, Any], physical: set[str], ephemeral: set[str]
) -> list[tuple[str, str]]:
    """TABLE-level ``(parent, child)`` edges over physical nodes only.

    Iterates ``parent_map`` in manifest order (deterministic output). For each
    physical child, each parent is resolved to its nearest **physical** ancestor(s)
    — recursing through ephemeral hops — so an ephemeral middle node's children
    connect straight to its physical ancestors. Non-candidate parents (tests /
    operations) are dropped. Edges are de-duplicated, first occurrence wins.

    The ephemeral→physical-ancestor resolution is **memoized** (``memo``): a shared
    ephemeral chain (many models selecting from the same ephemeral CTE) is walked
    once, not once per descendant.
    """
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    memo: dict[str, list[str]] = {}
    for child, parents in parent_map.items():
        if child not in physical:
            continue
        for ancestor in _physical_ancestors(parents, parent_map, physical, ephemeral, memo):
            edge = (ancestor, child)
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)
    return edges


def _physical_ancestors(
    parents: list[Any],
    parent_map: dict[str, Any],
    physical: set[str],
    ephemeral: set[str],
    memo: dict[str, list[str]],
) -> list[str]:
    """Resolve ``parents`` to physical ancestor uids, recursing through ephemerals."""
    resolved: list[str] = []
    for parent in parents:
        if parent in physical:
            resolved.append(parent)
        elif parent in ephemeral:
            resolved.extend(_ephemeral_ancestors(parent, parent_map, physical, ephemeral, memo))
    return resolved


def _ephemeral_ancestors(
    uid: str,
    parent_map: dict[str, Any],
    physical: set[str],
    ephemeral: set[str],
    memo: dict[str, list[str]],
) -> list[str]:
    """The physical ancestors reachable *through* the ephemeral node ``uid``, memoized.

    Cycle-safe: ``memo[uid]`` is seeded ``[]`` *before* recursing, so a self- or
    mutually-referential ephemeral chain (a malformed manifest) resolves to nothing
    at the back-edge instead of recursing forever. The real result overwrites the
    seed once the walk completes.
    """
    cached = memo.get(uid)
    if cached is not None:
        return cached
    memo[uid] = []  # cycle guard: a back-edge to `uid` sees this empty seed
    grand = parent_map.get(uid, [])
    result = (
        _physical_ancestors(grand, parent_map, physical, ephemeral, memo)
        if isinstance(grand, list)
        else []
    )
    memo[uid] = result
    return result


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""
