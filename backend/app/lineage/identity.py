"""Reconcile OpenLineage dataset identities across producers (ADR 0034 §6, #823).

ADR 0034 adopted the OpenLineage naming spec on the premise that DataQ's identifiers
would **join byte-for-byte** with other producers' — "a join, not a mapping layer".

**That premise is true for the namespace and false for the name.** Measured against
real `openlineage-dbt` 1.51.0, fed the real `manifest.json` from a real Snowflake
build:

    openlineage-dbt   snowflake://ACCT  ::  DATAQ_DB.ANALYTICS.mart_order_revenue
    DataQ             snowflake://ACCT  ::  DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE

Same physical table. Different bytes. `openlineage-dbt` formats a name as a bare
``".".join([database, schema, table])`` with **no case folding**, so it emits whatever
its source happened to spell — here `database`/`schema` from the dbt *profile* (typed
upper) and the table from the model *filename* (lower). The result is mixed case, per
segment. OpenLineage carries no case-folding rule, so nothing forces two producers to
agree.

Catalogs byte-match. So without this module a DataQ seed **404s against a perfectly
populated catalog**, and the pull is permanently, silently dark.

The fix is a **canonical fold applied only here, at the seam** — never in
`services.asset_identity`, which must keep producing the case the *engine's own*
catalog reports (that is what makes an asset identity correct in the first place).

The fold mirrors how each engine folds an **unquoted** identifier:

- ``snowflake://``      → UPPER  (Snowflake resolves unquoted identifiers to upper)
- ``unitycatalog://``   → lower  (UC/Spark resolve to lower)
- everything else       → **verbatim**

That last line is load-bearing. `abfss://`, `s3://` and Iceberg names are
**case-sensitive** — `raw/Orders.csv` and `raw/orders.csv` are different objects.
Folding those would not fix a mismatch, it would *invent* one, silently merging two
distinct files into one asset. When in doubt, do not fold.

Residual ambiguity, stated plainly: a Snowflake identifier that was *quoted* lowercase
(`"orders"`) is genuinely a different table from the unquoted `ORDERS`, yet both fold to
the same key. Callers must therefore **prefer an exact match and refuse to guess when a
fold key is ambiguous** — see `lineage.pull`.
"""

from __future__ import annotations

# Engine case-folding for unquoted identifiers, keyed by OL namespace scheme. Only the
# case-INSENSITIVE engines appear here; anything absent is left verbatim, which is the
# safe default (see the module docstring).
_FOLDS = {
    "snowflake://": str.upper,
    "unitycatalog://": str.lower,
}


def canonical_identity(namespace: str, name: str) -> tuple[str, str]:
    """The ``(namespace, name)`` two producers naming the same table must agree on.

    Applied to **both** sides of any cross-producer comparison, and used as the stored
    form of an asset discovered from a catalog — so a pulled dataset can never fork a
    second asset for a table DataQ already knows under the engine's own case.

    The namespace is passed through unchanged: it already matches byte-for-byte (that
    half of the ADR 0034 premise holds), and it is not an identifier the engine folds.
    """
    ns = namespace.strip()
    lowered = ns.lower()
    for prefix, fold in _FOLDS.items():
        if lowered.startswith(prefix):
            return ns, fold(name)
    return ns, name
