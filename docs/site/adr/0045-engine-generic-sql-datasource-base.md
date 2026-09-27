# ADR 0045 — Engine-generic SQL datasource base

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

## Amendment — Trino, the federation engine

Trino plugged in as one spec module (`datasources/trino.py`, the Apache-2.0 `trino` client and
its dialect) plus a registry entry. A connection pins one **catalog** (the base's `database`,
stored as `catalog`); the session URL carries `catalog/schema`, which is how a run is scoped.
It needed six small, generic base hooks — each one a fact about *some* engine, not a Trino
special case in the base:

- **`auth_connect_args`** — an engine whose driver authenticates with an auth OBJECT (basic, a
  JWT bearer) rather than a URL password. When set, the secret never enters the URL: the
  dialect would read a JWT from the URL's query, which SQLAlchemy does not mask.
- **`requires_secret` / `secret_optional`** — a config may authenticate with no stored secret
  (Trino `auth_type: none`); the adapter, runner builder, profiler, browser and dataset reader
  then proceed without one instead of reporting a missing credential.
- **`credential_expiry`** — a secret that states its own lifetime (a JWT's `exp`) feeds the
  existing credential-expiry signal on the connection.
- **`destination_fields`** per spec — the fields whose change requires re-entering the
  secret: for Trino, dropping TLS or trusting another CA (`sslmode`, `ca_bundle`) changes who
  can receive the secret as surely as the host does, and `auth_type` changes how it is sent (a
  stored password must never go out as a bearer token). A config whose auth mode needs a
  secret is refused on save when none is stored or supplied.
- **`names_are_lower_case`** — Trino folds every identifier, quoted or not, and its catalogs
  report them lower case, so a mixed-case catalog, schema or target is refused at save time;
  otherwise it could never join its enumerated asset or its schema-drift introspection.
- **`url_database`** — the same hook the MySQL engine uses.

Plus one base fix: the catalog queries' "no limit" bind value dropped from 2^62 to 2^31 - 1,
because Trino refuses `ORDER BY … LIMIT` above that (live-found).

**Decision 4 does not hold for Trino, and the docs say so.** Trino has no session or
transaction read-only switch a client can set, so DataQ cannot make the server refuse a write.
The guarantee is the Trino user's own access control (live-verified: a read-only file-based
rule refuses an `INSERT` with *Access Denied*), with the ADR 0019 validator in front of custom
SQL. TLS is `verify-full` or `disable` only — the client always verifies when TLS is on — and a
password or JWT over `disable` is refused at config validation, before the driver's own refusal.
GX already disables temporary tables on Trino, so uniqueness runs as one aggregate query and
needs no `temp_table_types` entry.

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
- A Trino connection covers whatever its catalog federates, so its checks are only as fast
  (and as costly) as the connector behind the catalog — a full scan of a Hive table is a
  full scan.
- Snowflake and Unity Catalog keep their own adapters; they are not migrated onto the base
  (their auth, catalogs and lineage differ too much to gain from it).
