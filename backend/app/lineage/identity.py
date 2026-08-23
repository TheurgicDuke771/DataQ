"""Reconcile OpenLineage dataset identities across producers (ADR 0034 §6, #823)."""

from __future__ import annotations

# Engine case-folding for unquoted identifiers, keyed by OL namespace scheme.
_FOLDS = {
    "snowflake://": str.upper,
    "unitycatalog://": str.lower,
}


def canonical_identity(namespace: str, name: str) -> tuple[str, str]:
    """The ``(namespace, name)`` two producers naming the same table must agree on."""
    ns = namespace.strip()
    lowered = ns.lower()
    for prefix, fold in _FOLDS.items():
        if lowered.startswith(prefix):
            return ns, fold(name)
    return ns, name
