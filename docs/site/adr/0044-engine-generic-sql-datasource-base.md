# ADR 0044 — Engine-generic SQL datasource base

- **Status:** Accepted (2026-09-27)
- **Date:** 2026-09-27
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (named for the engine, never a hosting vendor), [0019](0019-custom-sql-check-kind.md) (custom SQL), [0036](0036-connection-anchored-check-engines.md) (engines are connection capabilities — these offer GX only), [0040](0040-warehouse-inventory-sync-table-enumeration-seam.md) (table enumeration).

## Context

Theme 8 queues several SQL datasources that GX reaches through a SQLAlchemy dialect and DataQ
reaches with host / port / database / user + password: PostgreSQL first, then MySQL/MariaDB,
then Trino. Snowflake and Unity Catalog each got a hand-written adapter, runner and a set of
entries scattered across the capability sets (custom SQL, monitors, pushdown admission,
profiling, schema drift, inventory, browsing, asset identity). Copying that shape per engine
would make "add a datasource" a dozen-file edit every time — and one missed set is a feature
that silently doesn't work on the new engine.

## Decision

1. **One generic base** (`datasources/generic_sql.py`): a config model, a `ConnectionAdapter`,
   a `CheckRunner` and the catalog helpers, parameterized by a **`SqlEngineSpec`** — the only
   thing an engine supplies: its config subclass (TLS vocabulary, default port, identifier
   limit), driver name, GX datasource factory, `connect_args`, OpenLineage namespace scheme,
   whether the asset name carries the database, its catalog queries, and optional hooks
   (per-column aggregate capability for the profiler, run-path engine options).
2. **One registry** (`datasources/sql_engines.py`). Every capability set that names SQL
   datasources derives from `GENERIC_SQL_TYPES` instead of restating it. Adding an engine is
   one spec module plus one registry entry, a migration widening the connection-type CHECK,
   and the frontend form spec.
3. **Pushdown only.** Expectations run on one GX SQL batch, monitors as scalar Core
   aggregates; nothing is materialised in the worker, so the run target takes no sampling
   block and run admission treats the type as pushdown.
4. **Read only at the session.** Every session DataQ opens is read-only at the server
   (PostgreSQL `default_transaction_read_only`), so a side effect that slips past the ADR 0019
   validator still cannot write.
5. **Names are resolved exactly as spelled**, by the rule `sql.folding_identifier` already
   applies: an all-lower-case name is sent bare, anything else quoted. GX lower-cases an
   unquoted schema, so the GX session is **scoped to the target's schema** (a PostgreSQL
   `search_path`, re-validated through the identifier allowlist before it reaches the session)
   and GX gets no schema at all. Asset identity keeps parts verbatim, so a suite target and an
   enumerated table join byte-for-byte.
6. **Identifier limits are enforced at save time.** PostgreSQL silently truncates a name past
   63 characters, which would resolve a different object; the target resolver refuses it.
7. **Honest gaps, not silent ones.** No column-tag source (classification falls back to the
   suite policy and DataQ's own checks). No warehouse-native lineage: the engines get the
   ADR 0040 *enumeration* half through a separate `TableEnumerator` seam and stay out of
   `WAREHOUSE_LINEAGE_CONNECTION_TYPES`, so an empty lineage graph reads "not observed".
   Native engines: GX only.

## Amendment — MySQL / MariaDB, the second engine

MySQL / MariaDB plugged in as one spec module (PyMySQL — MIT; the GPL drivers are
excluded by ADR 0031) plus a registry entry, which is the test of decision 1. It needed
three small base hooks, all generic: `url_database` (a MySQL schema IS a database, so the
session connects to the target's schema to scope it), a shared `sslmode` vocabulary, and
**`temp_table_types`** — GX checks uniqueness on MySQL by building session temporary
tables, which a read-only transaction refuses, so that one type runs on its own GX
session with the read-only guard off (`connect_args(read_only=False)`). Nothing a user
wrote runs there; custom SQL stays on the guarded session. Decision 4 therefore reads
"read only except where the engine's own check implementation needs temporary
tables", and the docs say so.

## Consequences

- Every behaviour is verified against a **real server through the real driver** — for a
  value that crosses a driver boundary, only a live run is evidence: the
  PostgreSQL battery dogfoods the test suite's own database, as a least-privileged role, in
  CI. Two driver-boundary defects were found that way on the first run and are now hooks on
  the spec rather than special cases: `min(jsonb)`/`min(boolean)` do not exist (per-column
  profiler capability), and psycopg2's decoded JSON cells crash GX's result formatting
  (JSON kept as text on the run path).
- A new engine inherits the whole feature surface at once — which is the risk as well as the
  point: it is not "supported" until the same battery has run against it.
- A spec's hooks are only kept when a live run proves them: MySQL does not need
  PostgreSQL's JSON-as-text option (PyMySQL already returns JSON as text), so it has none.
- Snowflake and Unity Catalog keep their own adapters; they are not migrated onto the base
  (their auth, catalogs and lineage differ too much to gain from it).
