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

## Amendment — MySQL / MariaDB, the second engine

MySQL / MariaDB plugged in as one spec module (PyMySQL — MIT; the GPL drivers are
excluded by ADR 0031) plus a registry entry, which is the test of decision 1. It needed
three small base hooks, all generic: `url_database` (a MySQL schema IS a database, so the
session connects to the target's schema to scope it), a shared `sslmode` vocabulary, and
**`temp_table_types`** — GX checks uniqueness on MySQL by building session temporary
tables, which a read-only transaction refuses, so that one type runs on its own GX
session with the read-only guard off (`connect_args(read_only=False)`). Nothing a user
wrote runs there; custom SQL stays on the guarded session. MySQL's read-only and UTC
settings are two statements (the `transaction_read_only` *variable* is missing before
MariaDB 11.1, so the portable `SET SESSION TRANSACTION READ ONLY` form is used), which
PyMySQL's single `init_command` cannot carry — hence `session_statements`, applied to every
engine DataQ builds (`prepare_engine`) and, for GX, through a **`GxConnectionSource`**
handed to GX as its engines' `creator`. GX builds a fresh SQLAlchemy engine per execution
engine and never disposes it, so an event hook on the engine it exposes never reached the
one that validates — and each run left one idle server session behind until garbage
collection (live-found on PostgreSQL: three runs, three backends). The source closes every
connection a run opened. Decision 4 therefore reads
"read only except where the engine's own check implementation needs temporary
tables", and the docs say so.

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
- A spec's hooks are only kept when a live run proves them: MySQL does not need
  PostgreSQL's JSON-as-text option (PyMySQL already returns JSON as text), so it has none.
- A Trino connection covers whatever its catalog federates, so its checks are only as fast
  (and as costly) as the connector behind the catalog — a full scan of a Hive table is a
  full scan.
- Snowflake and Unity Catalog keep their own adapters; they are not migrated onto the base
  (their auth, catalogs and lineage differ too much to gain from it).

## Amendment — SQL Server (2026-09-27)

The SQL Server / T-SQL engine ([ADR 0044](0044-mssql-tds-driver-and-entra-auth.md)) is the
first engine that breaks three of the assumptions above, and each became an optional spec hook
rather than a special case:

- **§4 does not hold for it.** TDS has no session-level read-only setting, so a SQL Server
  session is *not* read-only at the server. The guard there is the ADR 0019 validator — now
  lexing each engine's own delimited identifiers (`[…]`, `` `…` ``) — plus a `db_datareader`
  login, which the docs require. The base's promise is restated per engine, not assumed.
- **§5's session scoping does not exist.** A login's default schema is fixed on the server, so
  `session_schema = False` hands GX the schema instead; SQL Server's usual case-insensitive
  collations resolve it whatever GX's casing (a mixed-case schema on a case-sensitive
  collation is a documented limitation).
- **One secret can be two kinds of credential.** An Entra service principal's client secret
  goes to a token endpoint, not the server, so the spec owns its URL and connect args (a
  subclass overriding `url` / `engine_args`), declares its own `destination_fields`, and names
  its secret (`credential_noun`).

Two more hooks came with it: `explain_failure`, which turns a failure the engine *knows* (a
documented driver limitation, a missing optional driver) into a DataQ-authored message shown
verbatim; and `unsupported_expectation_types`, for an allowlisted type the dialect has no GX
translation for (regex on T-SQL), refused at author time rather than erroring on every run.

## Amendment — Amazon Athena (2026-09-28)

Athena is the fifth engine and
the first with no server to open a session on. It needed two small hooks and one GX fix:

- **The endpoint is the region.** `AthenaConfig` derives `host` (`athena.<region>.amazonaws.com`)
  from `region` and refuses a configured host that disagrees, so a config can never point the
  credential somewhere its region field does not say. The IAM access key ID is the base's
  `user`; the secret access key travels as connect args, never in the URL.
- **`url_query`** (a config hook): the workgroup, data catalog and query-results location are
  non-secret driver options the URL's query carries.
- **`namespace_includes_port`** (a spec flag): OpenLineage's Athena namespace is
  `awsathena://athena.<region>.amazonaws.com`, with no port.
- **§4 does not hold**, as on Trino and SQL Server: Athena has no read-only session, so the
  guarantee is an IAM policy that can only read (live-verified: the reader's `CREATE TABLE`
  was refused). Query results are written to the results location, so region, workgroup and
  results location are destination fields.
- **GX had no Athena regex branch.** Every regex expectation errored on the pyathena dialect;
  `gx_metrics` adds one (`regexp_like`, Athena's engine being Trino's) to each GX module that
  imported the helper.

Every SQL-batch expectation type, custom SQL, the monitors, profiler, schema drift, comparison
reads, inventory, browsing and an end-to-end persisted run were **executed** against a live
Athena workgroup as a least-privileged IAM user. Every check is a billed query, which the guide
states.

## Amendment — Amazon Redshift (2026-09-28)

Redshift is the sixth engine. It speaks the PostgreSQL protocol, so §4 holds as on PostgreSQL:
the libpq startup options carry `default_transaction_read_only` and the `search_path`, and
Redshift honours both (a write by the reader was refused live). The dialect is
`redshift+psycopg2` from `sqlalchemy-redshift` (MIT), which GX's Redshift datasource requires;
every TLS mode verifies against the Amazon CA bundle it ships. Three hooks were added:

- **`gx_schema_with_session`** (a spec flag). GX's Redshift column-type lookup reads
  `information_schema.columns` by table name alone when it is given no schema, so a table with
  the same name in another schema merged its columns into the target's and every
  multi-column check failed on a column that did not exist. The session stays scoped to the
  schema and GX is handed it as well. That breaks this ADR's "never a lower-cased GX schema"
  rule only in form: Redshift names are lower case, and a test refuses the flag on an engine
  whose names are not.
- **`columns_view`** (a spec field). Schema drift reads `svv_columns` rather than
  `information_schema.columns`, which omits late-binding views. The profiler's type
  capabilities read it too.
- **`namespace_authority`** (a spec hook). OpenLineage names a Redshift dataset
  `redshift://<cluster>.<region>:<port>`; the cluster or workgroup and the region are read from
  an AWS endpoint, and any other host is kept as configured.

The catalog differs from PostgreSQL's: `pg_class` has no `relispartition`, `pg_type` no
`typcategory`, and a leader-node catalog cannot be joined to `svv_mv_info`, so a materialized
view is listed as a view. Every SQL-batch expectation type, custom SQL, the monitors, the
profiler over a table and a late-binding view, schema drift, comparison reads, inventory,
browsing and an end-to-end persisted run were **executed** against a Redshift Serverless
workgroup as a user granted only `USAGE` and `SELECT` on one schema.
