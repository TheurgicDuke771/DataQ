# Datasources & checks

## Supported datasources

| Datasource | Connection | Check authoring | Execution |
|---|---|---|---|
| Snowflake (DEV/QA/UAT) | account + user + key/PAT | ✅ | ✅ |
| ADLS Gen2 (flat files) — and ADLS-compatible endpoints such as [Fabric OneLake](#onelake-fabric-lakehouse-files) | account URL + container, SAS **or** Entra service principal | ✅ | ✅ |
| AWS S3 **and S3-compatible** (flat files) | bucket + region, access key (+ optional endpoint) | ✅ | ✅ |
| Unity Catalog (Databricks) | workspace URL + warehouse + PAT | ✅ | ✅ |
| Apache Iceberg | catalog URI + catalog type (REST/SQL/Glue/Hive) + optional storage credential | ✅ | ✅ |
| PostgreSQL (any server — self-hosted or a managed service) | host + port + database + user, password; TLS mode | ✅ | ✅ |
| MySQL / MariaDB (any server — self-hosted or a managed service) | host + port + database + user, password; TLS mode | ✅ | ✅ |
| Trino (any cluster, incl. Starburst — and every catalog it federates) | host + port + catalog + user; password, JWT or none; TLS + optional private CA | ✅ | ✅ |
| SQL Server / T-SQL (SQL Server, Azure SQL, Synapse; Fabric SQL via the ODBC lane) | host + port + database; SQL login (user, password) or Entra service principal (tenant + client ID, client secret) | ✅ | ✅ |
| Amazon Athena (serverless SQL over S3 through the Glue catalog) | region + IAM access key (key ID, secret access key); optional workgroup, query-results location, data catalog | ✅ | ✅ |
| Amazon Redshift (provisioned clusters and Serverless workgroups) | endpoint + port + database + user, password; TLS mode | ✅ | ✅ |

## Add a connection

Adding, editing, deleting or re-credentialing a connection requires the **Admin** workspace
role (ADR [0033](../adr/0033-workspace-roles-rbac.md)) — connections are shared infrastructure
holding credentials, and every suite in the workspace runs on them. Members and Viewers can
see and reference them, and Members can run the saved-connection **Test**.

In the UI, **Connections → Add connection**, pick the datasource, fill the type-specific
fields, and **Test** it (a live reachability probe). Credentials are stored in the secret
store (Azure Key Vault / AWS Secrets Manager / OpenBao, depending on deployment), never in the database.

### A connection is tested before it is saved

**Create** runs the same live test as the **Test** button first, and saves nothing if it fails:
the form shows the reason and the connection is not created. The same applies when you edit a
connection's settings or credential (a rename alone is not tested) and when you
**re-authenticate** — a new credential that does not work is refused, and the stored one stays
in place.

If the test fails for a reason that does not mean the connection is wrong, an Admin can choose
**Create without testing** (or **Save without testing** / **Rotate without testing**). Use it
when:

- the store is not reachable from the DataQ API right now, but will be from the workers that
  run the checks;
- an orchestrator (ADF, Airflow) is down while you register it;
- a **new dbt project** has not published a `run_results.json` yet — its test fails until the
  first build does (see [Orchestration](orchestration.md)).

A connection saved this way is not verified: its credential health reads *Unknown*, and the
audit log records that its test was skipped. Test it once the store is reachable. Over the API,
send `skip_test: true` with the create, update or re-auth request; see the
[REST API](../reference/rest-api.md#connections).

Snowflake supports two auth modes: **password** and **key pair (RSA)**. For key pair,
paste the PEM private key; if the key is passphrase-protected (PKCS#8), fill the optional
**Key passphrase** field — both parts are stored together as one secret and rotate
atomically via **Re-auth**. Leave the passphrase blank for an unencrypted key.
Key-pair connections also require **Role** (the GX key-pair form mandates one for suite
runs, so it is validated when the connection is saved).

ADLS Gen2 supports two auth modes: a **SAS token** (the default, and what every connection
created before this option existed uses) and an **Entra ID service principal** — the
directory (**Tenant ID**) and application (**Client ID**) IDs, plus the app's **client secret**
as the credential. A service principal needs a *data* role on the container (e.g. *Storage
Blob Data Reader*); **Test** lists one entry to prove it, because the container-properties call
alone also succeeds for a principal that holds only the control-plane *Contributor* role and
would then fail every read. DataQ cannot read a client secret's expiry — it lives in Entra ID —
so the connection card says **expiry not visible** instead of counting down; track it where the
secret was created and **Re-auth** before it lapses. A wrong or expired client secret is
recorded as a rejected credential; a wrong tenant or client ID, or a missing data role, is
reported as a configuration or permission problem instead.

### OneLake (Fabric lakehouse files)

Microsoft Fabric OneLake serves the same Blob API, so an **ADLS Gen2** connection reads a
lakehouse's `Files/` area — CSV, Parquet and JSON, with every flat-file check, freshness (including
arrival-time), volume, the profiler, browsing and batch targets. There is no separate
connection type:

| Field | Value |
|---|---|
| Account URL | `https://onelake.blob.fabric.microsoft.com` |
| Container | the Fabric **workspace** name or ID |
| Auth type | **Service principal** — OneLake does not accept a stored SAS (its SAS is user-delegated and lasts at most an hour) |
| Run target path | `<lakehouse>.Lakehouse/Files/<folder>/<file>`, e.g. `sales.Lakehouse/Files/orders/orders_2026-09-27.csv` |

Prerequisites, in Fabric: the tenant setting *Service principals can use Fabric APIs* must be
enabled, and the service principal must be a member of the workspace (Viewer is enough to
read). OneLake reports a workspace the principal cannot see as **not found**, so a
"table/path does not exist" failure on a correct workspace name usually means a missing
workspace role. A batch target's prefix must reach inside an item
(`<lakehouse>.Lakehouse/Files/...`): OneLake refuses a flat listing of the workspace root,
though **Browse** starts there fine. Assets from OneLake are named
`abfss://<workspace>@onelake.dfs.fabric.microsoft.com/...`. Only public-cloud Entra ID is
supported for the token (`login.microsoftonline.com`).

### Iceberg on an S3-compatible store

For MinIO, R2, Ceph and similar, set the storage properties on the connection:
`s3.endpoint` (e.g. `http://minio:9000`), `s3.access-key-id` and `s3.path-style-access=true`,
with the secret key as the stored credential (`secret_property: s3.secret-access-key`). If
reads fail with `ACCESS_DENIED` while the store accepts the key elsewhere, add `s3.region`
(any region the store accepts, e.g. `us-east-1`): pyarrow's S3 client signs with a region,
and some stores check it.

**Test connection** lists the catalog's namespaces and then loads the first table it finds,
which reads that table's metadata file from the warehouse with the storage credential. A wrong
storage credential therefore fails the test. On a catalog with no tables yet, only the catalog
itself can be checked.

### Moving a connection to a new host

Editing a field that decides *where* the credential is sent — Snowflake `account`, ADLS
`account_url` / `auth_type` / `tenant_id` / `client_id`, S3/dbt `endpoint_url`, Unity Catalog `workspace_url`, Iceberg `catalog_uri` /
`warehouse` / `properties` / `secret_property`, PostgreSQL / MySQL `host` / `port`, Trino
`host` / `port` / `sslmode` / `ca_bundle` / `auth_type`, SQL Server `host` / `port` / `auth_type` /
`tenant_id` / `client_id` / `ca_bundle` / `driver`, Airflow
`base_url`, dbt `artifacts_uri` —
requires re-entering
that credential in the same save. The edit form asks for it as soon as you change one of
those fields; through the API the request is rejected with `422 credential_redirect` until
you include it.

This is deliberate. A stored credential is never forwarded to a destination the caller
changed, so an Admin who may *rotate* a credential still cannot *read* one by pointing the
connection at a host they control and pressing Test. Moving a connection is fully supported —
doing it with a credential you don't know is not.

### Credential health

A datasource connection reports whether its stored credential is still being **accepted**,
so an expired or revoked one is visible without anyone pressing Test. It has three states:

| State | Meaning |
|---|---|
| **Unknown** | The credential has not been used yet. Nothing has run, dry-run, profiled or tested against this connection, so DataQ has observed nothing about it. |
| **Healthy** | The datasource accepted the credential the last time DataQ used it. |
| **Failing** | The datasource **rejected** the credential. The connection also shows how many consecutive rejections it has had, and a classified reason — never raw driver text, which can carry a token or DSN fragment. |

**Unknown is not a clean bill of health**, and it is never silently upgraded to healthy: a
connection nothing uses stays unknown indefinitely. That is deliberate — the signal is
derived from work DataQ already does rather than from a periodic probe, so it costs no
warehouse credits and cannot report on a connection nobody has exercised.

Only credential **rejections** move this signal. A missing SELECT grant, an unreachable
host and a bad table name all leave it untouched, because none of them says the credential
is dead — those surface as the run's own failure reason instead.

Re-authenticating a connection (which tests the new credential first), or a passing **Test
connection**, clears the signal immediately — a credential rotated in *without* testing resets
it to **Unknown** instead; you do not have to wait for the next scheduled run to confirm a rotation
worked. Workspace admins see every datasource connection's credential health together on
the admin health view.

Orchestration providers (ADF, Airflow, dbt) do not carry this signal — their equivalent is
polling health, which is reported separately.

### S3-compatible object stores

The **S3** connection is not AWS-only. Leave **Endpoint URL** blank and it addresses AWS
exactly as before; set it to a store that speaks the S3 API — MinIO, Ceph/RadosGW,
Cloudflare R2, Wasabi, Backblaze B2, SeaweedFS, or an on-prem gateway — and the same
connection, checks and monitors work unchanged.

- **Endpoint URL** — the full base URL including scheme, e.g.
  `https://minio.example.com:9000`. A URL without `http://`/`https://` is rejected at
  save time rather than failing later as an opaque connection error.
- **Addressing style** — `auto` (default), `path` or `virtual`. `auto` uses **path**
  addressing whenever an endpoint is set, which is what MinIO and SeaweedFS require;
  with no endpoint it leaves AWS's default untouched. Override it only if your store
  needs the other form (R2 and Wasabi accept virtual-host addressing).
- **Region** is still required — S3-compatible stores generally ignore it, but the AWS
  SDK requires a value. `us-east-1` is the conventional filler.
- The endpoint must **not** embed a credential (`https://key:secret@host`). Connection
  `config` is stored and returned in plaintext, so a credential there would live outside
  the secret store; it is rejected at save time. Put the key in **Access key ID** and the
  secret in the credential field.
- An `http://` endpoint is accepted (in-cluster MinIO usually has no TLS), but object
  bytes and request signatures then travel **cleartext** — prefer `https://` for anything
  crossing a network you don't control.

**Asset identity.** With no **Endpoint URL** (AWS), the asset namespace is exactly
`s3://{bucket}` — unchanged, since AWS bucket names are globally unique and this form
is already persisted on every S3 asset in production. With an **Endpoint URL** set, the
namespace becomes `s3://{host[:port]}/{bucket}` — the endpoint's authority (scheme
stripped, default port elided, host lower-cased) joins the bucket, so two stores that
happen to share a bucket name (an AWS bucket and a MinIO bucket both named `landing`,
say) resolve to *different* assets instead of merging their scorecards, lineage and
incidents (decided in
[ADR 0040](../adr/0040-warehouse-inventory-sync-table-enumeration-seam.md) §6).

The same two fields exist on a **dbt** orchestration connection whose `artifacts_uri` is
`s3://…`, so the artifacts poll can read from the same store.

### PostgreSQL

One connection type for **any PostgreSQL server** — self-hosted, or a managed service on any
cloud. It is named for the engine, never for a vendor that hosts it (ADR
[0010](../adr/0010-provider-agnostic-infrastructure-seams.md)), and is the first engine on
DataQ's generic SQL datasource base, which the MySQL/MariaDB, Trino, SQL Server, Athena and
Redshift adapters reuse.

- **Fields:** host (a bare hostname or IP — no scheme, port or path), port (default 5432),
  database, user, an optional default schema (default `public`: where a target with no
  schema resolves), and the password as the connection's secret.
- **TLS:** `require` by default. `verify-full` / `verify-ca` also check the server
  certificate, against the system trust store. `disable` must be chosen explicitly; libpq's
  `prefer` — which silently falls back to plaintext — is not offered.
- **Read only, always.** Every session DataQ opens sets `default_transaction_read_only`, so
  the server itself refuses a write, whatever a query contains. Give DataQ its own login role
  anyway, with no more than it needs:

  ```sql
  CREATE ROLE dataq_reader LOGIN PASSWORD '…';
  GRANT CONNECT ON DATABASE shop TO dataq_reader;
  GRANT USAGE ON SCHEMA sales TO dataq_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA sales TO dataq_reader;
  ```

- **Name resolution.** Each session's `search_path` is `pg_catalog`, then the target's schema,
  then `public` — so an unqualified name in custom SQL resolves in the table's own schema, and
  extension objects installed in `public` (citext, pg_trgm, PostGIS) still work. `pg_catalog`
  comes first on purpose: a function planted in a schema DataQ reads can never override a
  built-in DataQ's own SQL calls. Still, don't give untrusted roles `CREATE` on schemas DataQ
  checks — a function with a *different* signature there is still callable by name.
- **Everything runs in the database** — expectations on a SQL batch, monitors as scalar
  aggregates, custom SQL as-is — so no rows are loaded into the worker and a run target
  takes no sampling block.
- **The profiler** reports min/max as unavailable (null) for types PostgreSQL has no MIN/MAX
  for — `boolean`, `json`/`jsonb`, `uuid`, geometric — and distinct count / top values as
  unavailable for types with no equality (`json`, `xml`, geometric), instead of failing the
  whole profile. A domain is judged by the type it is a domain over, and an array by its
  element type. A JSON cell in a failing-row sample is shown as its JSON text.
- **No column tags.** PostgreSQL has no column-tag feature for DataQ to read, so its columns
  are classified by the suite's column policy and DataQ's own name/value checks only — see
  [security](../security/overview.md).
- **Inventory sync** enumerates the tables, views, materialized views and foreign tables the
  user can `SELECT`, in schemas it has `USAGE` on (partitions are left out — their parent is
  what a check targets). There is **no warehouse-native lineage**: PostgreSQL keeps no lineage
  log to read, so a PostgreSQL asset's lineage comes from dbt, OpenLineage or a catalog, and
  an empty graph means "not observed", not "nothing feeds this table".

### MySQL / MariaDB

One connection type for **any MySQL or MariaDB server**, on the same generic SQL base as
PostgreSQL. The driver is **PyMySQL** (MIT); the GPL-licensed MySQL drivers are never used.
Works with MySQL 8 and MariaDB 10.6 and later, including the long-term line many managed
services still default to.

- **Fields:** host, port (default 3306), database, user, and the password as the secret. A
  MySQL *schema* is a database, so a run target's optional schema names another database
  (it defaults to the connection's).
- **TLS:** the same modes as PostgreSQL, `require` by default. `require` encrypts and refuses a
  server without TLS (PyMySQL's own default would quietly fall back to plaintext); `verify-*`
  also checks the certificate against the system trust store, so a server with MySQL's
  self-generated certificate fails them.
- **Read only, UTC.** Every session runs `SET SESSION TRANSACTION READ ONLY` (the statement
  every version accepts — the `transaction_read_only` variable does not exist before
  MariaDB 11.1) and sets `time_zone = '+00:00'`, so a
  `TIMESTAMP` column comes back in UTC and freshness is right whatever the server's zone is
  (`DATETIME` has no zone and is read as UTC). **One exception:** GX checks uniqueness on
  MySQL by copying the column into session temporary tables, which a read-only transaction
  refuses — so *Column values unique* runs on its own session without the read-only guard.
  No SQL you wrote ever runs there. It needs the `CREATE TEMPORARY TABLES` grant; without it
  that one check errors and the rest of the suite is unaffected:

  ```sql
  CREATE USER 'dataq_reader'@'%' IDENTIFIED BY '…';
  GRANT SELECT, SHOW VIEW, CREATE TEMPORARY TABLES ON shop.* TO 'dataq_reader'@'%';
  ```

- **Collation decides equality.** Uniqueness, set membership and comparisons follow the
  column's collation: under the default case-insensitive one, `'a'` and `'A'` are duplicates.
  Column names are case-insensitive; **table and database names are case-sensitive** on a Linux
  server (`lower_case_table_names=0`), so type them exactly as the server shows them.
- **Types:** `BOOLEAN` is `TINYINT(1)` (values `0`/`1`); MariaDB's `JSON` is `LONGTEXT`, which is
  what schema drift reports for it.
- **No column tags, no warehouse-native lineage** — as for PostgreSQL. Inventory sync and the
  schema browser list what `information_schema` shows the user, which is privilege-filtered.

### Trino

One connection type for **any Trino cluster** — including Starburst — and through it every
store the cluster federates (Hive, Iceberg, Delta, PostgreSQL, MySQL, Cassandra, MongoDB,
Kafka topics…) with no per-store adapter on DataQ's side. It is the third engine on the
generic SQL base. Supports HTTPS (including a private CA), password and JWT authentication,
and whatever access control the cluster enforces.

- **Fields:** host, port (default 443, or 8080 with TLS disabled), **catalog**, user, an
  optional default schema (default `default`), and the auth type with its secret. A
  connection reads **one catalog** — the counterpart of a PostgreSQL database — so a target
  is `schema.table` inside it and its asset is `catalog.schema.table`. Add one connection per
  catalog you want to check.
- **Authentication:** `password` (Trino's PASSWORD authenticator — a password file, LDAP, …;
  sent as HTTP basic), `jwt` (a bearer token; DataQ reads its `exp` and shows when it
  expires, so rotate it with **Re-authenticate** before then), or `none` for a cluster
  without authentication (no secret is stored; Trino still needs a user name). A password or
  token is **only ever sent over TLS**: `password`/`jwt` with TLS disabled is refused when
  you save, before anything is sent.
- **TLS:** `verify-full` by default — the certificate and the host name are always checked;
  there is no mode that encrypts without verifying. For a certificate from a **private CA**,
  paste the CA's PEM into **CA bundle**: it replaces the system trust store for this
  connection. Changing the host, port, TLS mode, CA bundle or auth type counts as moving the
  credential, so the edit asks for it again (switching to `none` asks for nothing — the stored
  one is simply no longer sent). The CA must be a well-formed X.509 CA (with a
  `keyUsage` extension) — the worker's TLS stack verifies strictly.
- **Not read-only at the session — the credential is the guarantee.** Trino has no session
  or transaction read-only switch a client can set, so unlike PostgreSQL and MySQL, DataQ
  cannot make the server refuse a write. Custom SQL still passes DataQ's validator, which
  rejects writes and DDL — but the guarantee rests on **the Trino user DataQ connects as**:
  give it read-only access in the cluster's access control, e.g. with file-based rules:

  ```json
  {
    "catalogs": [{"user": "dataq_reader", "catalog": "hive", "allow": "read-only"}],
    "tables": [{"user": "dataq_reader", "schema": "sales", "privileges": ["SELECT"]}]
  }
  ```

  Listings (the schema browser, inventory sync) show what that access control lets the user
  see.
- **Names are lower case.** Trino folds every identifier to lower case — quoted or not — and
  its catalogs report them that way, so the catalog, schema and table must be typed in
  lower case (a mixed-case name is refused when you save). A connector over a store with
  mixed-case names (PostgreSQL, MySQL) presents them lower-cased; on those catalogs enable the
  connector's `case-insensitive-name-matching` so Trino can resolve them. A mixed-case
  **column** name in a check works — it folds like any other.
- **Time zones.** Every session runs in UTC, so a `timestamp` (no zone) column is read as
  UTC wall-clock time; `timestamp with time zone` carries its own zone. Freshness is right
  either way.
- **Everything runs on the cluster** — expectations on a SQL batch (GX never creates
  temporary tables on Trino; uniqueness is one aggregate query), monitors as scalar
  aggregates, custom SQL as-is. A check costs whatever the query costs the catalog behind
  it — a full scan of a large Hive table is a full scan.
- **Comparisons are byte-wise:** `'a'` and `'A'` are different values (unlike MySQL's default
  collation).
- **The profiler** reports min/max, distinct count and top values as unavailable (null) for
  types Trino cannot order — `json`, `map`, and anything containing one (`array(json)`, a
  `row` with a `map` field), plus the sketch/digest and geometry types.
- **No column tags, no warehouse-native lineage.** DataQ reads no column-classification
  source from Trino, and Trino keeps no lineage log DataQ reads — lineage comes from dbt,
  OpenLineage or a catalog, and an empty graph means "not observed".

### Amazon Athena

Serverless SQL over data in S3, through the AWS Glue catalog. It is an engine on the generic
SQL base, like Trino (Athena's own engine is Trino-based).

- **Fields:** the **region** (the endpoint follows from it — there is no host or port), the
  IAM **access key ID** with the **secret access key** as the stored secret, and optionally a
  **workgroup** (default `primary`), a **query-results location** (`s3://bucket/prefix/`;
  needed unless the workgroup enforces its own), a **data catalog** (default
  `awsdatacatalog`, the Glue catalog) and a **default database**. A connection reads one data
  catalog; a Glue database is the schema, so a target is `database.table` and its asset is
  `catalog.database.table`.
- **Every check is a billed query.** Athena charges per byte scanned, with a per-query
  minimum, and every check, monitor, profile, browse listing and inventory sync is a query.
  Each also takes seconds: a 24-check suite runs for a few minutes. Prefer Parquet or ORC
  tables and partitions, which Athena scans far less of.
- **Not read-only at the session — the IAM policy is the guarantee.** Athena has no
  read-only session. Custom SQL still passes DataQ's validator, which rejects writes and DDL,
  but give DataQ an IAM principal that can only read:

  ```json
  {
    "Statement": [
      {"Effect": "Allow", "Resource": "*", "Action": ["athena:StartQueryExecution",
        "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution",
        "athena:GetWorkGroup", "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable",
        "glue:GetTables", "glue:GetPartitions"]},
      {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
       "Resource": "arn:aws:s3:::your-bucket"},
      {"Effect": "Allow", "Action": ["s3:GetObject"],
       "Resource": "arn:aws:s3:::your-bucket/your-data/*"},
      {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
       "Resource": "arn:aws:s3:::your-bucket/athena-results/*"}
    ]
  }
  ```

  Query results land in the results location, so changing the region, workgroup or results
  location counts as moving the credential, and the edit asks for it again.
- **Names are lower case.** Glue folds every name to lower case, so the database and table
  must be typed that way (a mixed-case name is refused when you save).
- **Type names** for `to_be_of_type` are the bare Athena names: `DECIMAL`, `TIMESTAMP`,
  `VARCHAR`, `BIGINT`.
- **No column tags, no warehouse-native lineage.** Lineage comes from dbt, OpenLineage or a
  catalog, and an empty graph means "not observed".

### Amazon Redshift

Provisioned clusters and Redshift Serverless workgroups, on the generic SQL base. Redshift
speaks the PostgreSQL protocol, so it behaves like the PostgreSQL connection in most respects.

- **Fields:** the **endpoint** host (the cluster's or workgroup's, without `:5439/dev`), port
  (default 5439), database, a database user, an optional default schema (default `public`),
  and the user's password as the connection's secret. IAM authentication is not supported:
  create a database user for DataQ.
- **TLS:** `require` by default, and every TLS mode checks the server's certificate chain
  against Amazon's certificate authorities. `verify-full` also checks the hostname.
- **Read only, always.** As on PostgreSQL, every session sets `default_transaction_read_only`,
  so Redshift itself refuses a write. Give DataQ a user that can only read what it checks:

  ```sql
  CREATE USER dataq_reader PASSWORD '…';
  GRANT USAGE ON SCHEMA sales TO dataq_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA sales TO dataq_reader;
  ```

  `GRANT … ON ALL TABLES` covers only the tables that exist when you run it; add
  `ALTER DEFAULT PRIVILEGES IN SCHEMA sales GRANT SELECT ON TABLES TO dataq_reader` for tables
  created later.
- **Names are lower case.** Redshift folds every name to lower case, so schemas and tables must
  be typed that way (a mixed-case name is refused when you save). A cluster that enables
  `enable_case_sensitive_identifier` is not supported.
- **Views.** Late-binding views (`WITH NO SCHEMA BINDING`) work everywhere, including the
  profiler and schema drift. A **materialized view is listed as a view** when you browse or in
  the inventory: Redshift's system catalog cannot tell the two apart in a single query.
- **The profiler** reports min/max as unavailable for `BOOLEAN`, `SUPER`, `GEOMETRY`,
  `GEOGRAPHY` and `HLLSKETCH` (Redshift has no MIN/MAX for them, and MIN over `SUPER` returns
  nothing useful), and distinct count / top values as unavailable for the spatial and sketch
  types, which have no equality.
- **No column tags, no warehouse-native lineage.** Lineage comes from dbt, OpenLineage or a
  catalog, and an empty graph means "not observed".

### SQL Server / Azure SQL / Fabric (T-SQL)

One connection type (`mssql`) for **anything that speaks SQL Server's TDS protocol** — SQL
Server itself, Azure SQL Database, Synapse dedicated pools, and the Microsoft Fabric SQL
endpoints (Warehouse, Lakehouse SQL analytics endpoint, SQL database in Fabric). It is named for
the engine, not a cloud (ADR [0010](../adr/0010-provider-agnostic-infrastructure-seams.md)),
and sits on the same generic SQL base as PostgreSQL. The driver decision and its trade-offs are
[ADR 0044](../adr/0044-mssql-tds-driver-and-entra-auth.md). SQL Server, Azure SQL Database and
Synapse work with either auth mode on either driver lane; Microsoft Fabric Warehouse and Lakehouse
SQL analytics endpoints work on the ODBC lane.

- **Fields:** host (a bare hostname — e.g. `myserver.database.windows.net`; no scheme, port or
  `\instance`: connect to a named instance by its port), port (default 1433), database, an
  optional default schema (default `dbo`), and one of two **auth modes**:
    - **SQL login** — `user`, and the password as the secret. SQL Server, Azure SQL, Synapse.
    - **Entra service principal** — `tenant_id` and `client_id`, and the client secret as the
      secret. DataQ asks Entra ID for a token for `https://database.windows.net/` and presents
      it at login; the client secret never goes to the database server. Azure SQL, Synapse and
      Fabric. The principal must exist in the database (`CREATE USER [app-name] FROM EXTERNAL
      PROVIDER`) with read access. DataQ cannot read a client secret's expiry — track it in
      Entra ID and re-authenticate before it lapses. Managed identity and certificate
      credentials are not supported yet.
- **TLS is always on and always verified.** There is no TLS mode to choose: every connection is
  encrypted, the server certificate must chain to a trusted CA (the public roots DataQ ships,
  or a **private CA certificate** you paste into the connection for a self-hosted server), and
  the hostname must match the certificate. Connecting by IP address fails for that reason — use
  the name on the certificate. The shipped driver speaks TLS 1.2; TDS 8 "strict" encryption is
  not supported yet.
- **Read only — by grant, not by session.** Like Trino and unlike PostgreSQL and MySQL, SQL Server has no
  per-session read-only switch, so DataQ **cannot make the server refuse a write**. The guards
  are the custom-SQL validator (which understands T-SQL `[bracket]` identifiers and refuses
  `OPENQUERY`/`OPENROWSET`/`OPENDATASOURCE`, `BULK`, `DBCC`, `WAITFOR`, `BACKUP`/`RESTORE`
  and the rest of the ADR 0019 list) and **the login you give DataQ, which must be read-only**:

  ```sql
  -- in the target database
  CREATE USER dataq_reader FOR LOGIN dataq_reader;   -- or FROM EXTERNAL PROVIDER for Entra
  ALTER ROLE db_datareader ADD MEMBER dataq_reader;
  ```

  Nothing else is needed: the session-scoped `#temp` tables some SQL Server queries use need no
  extra grant (verified with a `db_datareader`-only login).
- **Two driver lanes** (`driver`):
    - **`python-tds`** (default) — pure-Python and MIT-licensed, shipped in the DataQ image. It
      covers SQL Server, Azure SQL and Synapse.
    - **`odbc`** — Microsoft ODBC Driver 18 through `pyodbc`. DataQ **does not ship** this
      driver (its licence does not allow us to redistribute it — ADR 0044), so this lane only
      works in an image **you** build on top of DataQ's — a sketch (DataQ's CI does not build
      or test it, since doing so would mean accepting the driver's licence):

      ```dockerfile
      FROM ghcr.io/theurgicduke771/dataq-backend:latest
      USER root
      RUN apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
            msodbcsql18 unixodbc && pip install pyodbc && rm -rf /var/lib/apt/lists/*
      USER 10001
      ```

      (add Microsoft's apt repository first, as Microsoft's install guide describes). Pick
      `odbc` on the connection; an optional `odbc_driver` names a different installed driver.
      If the lane is chosen but the driver or `pyodbc` is missing, **Test** says exactly that
      and how to fix it. On this lane the driver's own certificate checking applies (encryption
      required, server certificate and hostname verified against the image's trust store —
      connecting by IP is refused here too). A service principal still logs in with a token
      DataQ requests from Entra ID, so a wrong client secret fails at once with Entra's own
      error rather than a login timeout.
- **Microsoft Fabric SQL endpoints need the ODBC lane today.** Over the default `python-tds`
  driver, Fabric rejects the login after routing it (a known incompatibility in that driver,
  still being worked on); Test and runs say so and point at the ODBC lane instead of showing the
  raw driver error. Fabric accepts Entra ID only, and
  two things are set up on the Fabric side: the tenant setting **Service principals can use
  Fabric APIs**, and a workspace role (or item permission) for the principal. If Fabric refuses
  the principal's login on the ODBC lane, Test names those two prerequisites.

  On a Fabric Warehouse or Lakehouse SQL endpoint, **seven expectation types are not
  available**: *Column values unique*, *Compound columns unique*, *Values unique within
  record*, *Column A greater than B*, *Column pair equal*, *Column pair in set* and
  *Multicolumn sum*. Great Expectations evaluates them on SQL Server through a temporary table,
  which Fabric refuses, so the check editor does not offer them on a Fabric connection, and
  saving one through the API or MCP is refused with the reason. Use custom SQL instead, e.g.
  `SELECT id FROM {batch} GROUP BY id HAVING COUNT(*) > 1` for uniqueness. A Fabric endpoint's
  first login after it has been idle can take longer than Test Connection's 10 seconds — test
  again; runs wait up to a minute.
- **Everything runs in the database**, as on PostgreSQL. A run target takes no sampling block
  (so no `TABLESAMPLE`). Freshness reads `datetimeoffset` as the instant it is (offset
  honoured), `datetime2`/`datetime` as UTC, and `date` as midnight UTC.
- **Regular expressions are not available**: T-SQL has no regex operator Great Expectations can
  translate to, so the four regex expectations are refused when you save them (and hidden in
  the editor). Use a custom-SQL check with `LIKE` or `PATINDEX`. Value-length checks use
  T-SQL `LEN`, which ignores trailing spaces.
- **Type checks** (`to_be_of_type`, `in_type_list`) compare the bare type name: `DECIMAL` for
  `decimal(12,2)`, `INTEGER` for `int`, `NVARCHAR`, `DATETIME2`, `DATETIMEOFFSET`, `BIT`.
- **The profiler** reports min/max as unavailable (null) for types SQL Server has no MIN/MAX for
  — `bit`, `xml`, `geography`/`geometry`, `text`/`ntext`/`image`, `json`, `vector` — and distinct
  count / top values as unavailable for all of those except `bit`.
- **Names** resolve under the database's catalog collation. On the usual case-insensitive
  catalogs any casing reaches the object. On a **case-sensitive** one — a Fabric Warehouse by
  default, or an Azure SQL database created with a case-sensitive catalog collation — type
  schema, table and column names exactly as they are stored; mixed-case names are resolved as
  spelled.
- **Comparison queries** are read as a derived table, which SQL Server constrains: name every
  computed column (`amount * 2 AS doubled_amount` — an unnamed one is refused with that fix),
  and write a common table expression as a subquery (a query starting with `WITH` is refused
  when you save). A trailing `ORDER BY` is fine; DataQ reads it with `OFFSET 0 ROWS`, and the
  comparison orders rows itself.
- **No column tags.** DataQ does not read SQL Server's sensitivity classifications
  (`sys.sensitivity_classifications`) yet — reading them needs a permission a reader login
  usually lacks, and without it the catalog view silently returns nothing, which DataQ would
  otherwise report as "no sensitive columns". Columns are classified by the suite's column
  policy and DataQ's own checks. There is no warehouse-native lineage either.
- **Inventory sync and browsing** list the tables and views in `INFORMATION_SCHEMA` that the
  login can `SELECT`; the fixed-role schemas every database carries (`db_datareader`, …) are
  never offered.
- **Speed.** The shipped driver is pure Python. DataQ pushes aggregates to the server and caps
  samples, so this rarely matters, but every query is one network round trip — a suite run
  from a region far from the database is dominated by latency, not by the database.
- **Azure SQL serverless** databases pause when idle. The first login resumes them, which can
  take up to a minute: a run waits for it, a Test Connection gives up after 10 seconds — test
  again once the database is awake.

### Identifier casing (Snowflake / Unity Catalog / PostgreSQL)

Warehouses fold **unquoted** identifiers — Snowflake upper-cases them — so a column
created as `order_ts` is really stored as `ORDER_TS`, while one created as
`"Amount"` is stored mixed-case and is only reachable quoted.

DataQ handles both: type the name **exactly as the column dropdown reports it**.
Lower-case names are sent unquoted and fold as the warehouse always folded them;
anything else is quoted for you, using the right quote character for the engine
(Snowflake `"`, Databricks backticks). This applies to every SQL path — profiling,
the aggregate/top-values queries, and freshness/volume monitors alike.

**Still unsupported**, and refused with a 422 rather than silently mis-resolved:

- Identifiers needing quotes for reasons other than case — spaces, dots, leading
  digits, non-ASCII characters.
- Genuinely **reserved words** (`order`, `select`, …) used as a column or table
  name. An unquoted `order` is stored `ORDER`, which neither `order` (parse error)
  nor `"order"` (wrong case) reaches.

In both cases, alias the column in a view and point the check at that.

**PostgreSQL** folds unquoted names to *lower*-case, so the same rule gives the natural
result there: `order_id` is sent bare, while `CustomerId` or `Sales` (created quoted) are
quoted and matched exactly. PostgreSQL silently **truncates** a name longer than 63
characters, which could resolve a different object — so a longer table or schema name is
refused when the suite is saved.

One more caveat: in a **three-part** `catalog.schema.table` target, only the table
gets quoted — a mixed-case *catalog or schema* still folds. This affects nobody
today (Unity Catalog is the only three-part datasource and it resolves identifiers
case-insensitively), but don't rely on it if that changes.

### Seeing coverage: the asset scorecard

Because every check carries a dimension, the **asset page** shows a *Data quality
by dimension* panel: per-dimension score and check counts, plus — the part worth
looking at — the dimensions with **no checks at all**.

Three states, deliberately kept distinct:

| What you see | What it means |
|---|---|
| A score bar | Checks exist and evaluated in the latest run. |
| **No signal** | Checks exist, but none evaluated — not yet run, or all skipped/errored. Not 0%: nothing was measured. |
| Listed under **Not covered** | No checks for that dimension exist at all. Not 0%, and *definitely* not 100%. |

**Coverage counts checks, not runs.** A check you author today counts as coverage
immediately — it does not need a completed run first, and a suite whose latest run
failed does not lose its coverage. The score is the part that waits for a run.

The `3/5 passing` figure counts checks that passed in the latest run out of checks
that exist, so the gap includes failing, skipped, errored **and** never-run checks;
hover it to see how many were excluded from the score.

The numbers are **workspace-wide**: everyone who can see the asset sees the same
score, whether or not they can open the suites behind it. Two people comparing
notes on the same table should never see two different verdicts.

Checks with no dimension set are counted separately ("N checks have no dimension
set") rather than filed under a dimension — otherwise "Not covered" would be wrong.

### Automatic coverage: watch every table without writing checks

Switch on **Automatic coverage** on a Snowflake, Unity Catalog or SQL-database connection
(PostgreSQL, MySQL/MariaDB, Trino, SQL Server, Athena, Redshift) and every table its inventory
lists gets watched, with no check written by hand. Only a workspace admin can switch it on, like
any connection change.

For each table, DataQ creates one suite marked **Automatic**, named `Auto: <table>`, holding
checks that compare the table with its own history rather than asserting anything about the
data:

| Check | What it reports |
|---|---|
| Row count is normal | The row count against the same weekday in earlier weeks. |
| Data is fresh | The age of the newest value in a load or event timestamp column, against its history. |
| Schema is unchanged | Any added, removed or retyped column. |
| Column profile is normal | Every column's null rate and distinct count against their own history (see *Column profile* under the anomaly monitor). |

The row-count, freshness and column-profile checks are anomaly checks: they warn at 3 standard deviations from the table's history,
fail at 4 and go critical at 6, and they skip until the table has four earlier runs on the same
weekday, so a new suite starts scoring in its fifth week. The freshness check appears after the
first run, once the schema check has recorded the columns: DataQ prefers a column such as
`loaded_at` or `updated_at`, and if the table has no timestamp column it says so instead of
guessing. A schema change fails.

For example, with coverage on for a connection whose inventory lists `shop.public.orders`, the
next daily pass creates *Auto: shop.public.orders* with the row-count, schema and column-profile
checks and a daily schedule. After its first run it adds *Data is fresh* on `ordered_at`.

- **Suggested rules wait for you.** Once a week DataQ profiles each covered table and proposes
  rules that assert something about its data: *never null* for a column with no nulls in at
  least 100 rows, *unique* for an id-like column whose values are all distinct, and *one of these
  values* for a column with 2 to 10 distinct values. They appear under **Suggested rules** on the
  suite page, each with the reason (for example "No nulls in 12,480 rows"). Accepting one adds the
  check; a rejected rule is never suggested again. A column the suite's policy or the warehouse
  marks sensitive is never proposed as a value set, because that would copy its values into the
  check.
- **Your changes win.** You can edit, snooze or share these suites and checks like any other.
  DataQ only ever adds a missing check. It never changes one you edited, and a check you delete
  is not added back.
- **Leaving a table out.** On the asset page, an admin can switch off *Include in automatic
  coverage*. The table's suite is paused, not deleted, and its history stays. Switching coverage
  off for the whole connection pauses all of its automatic suites the same way.
- **Cost.** Each covered table runs its checks once a day, at a time spread across the day by
  table. The row-count and freshness checks are cheap aggregates, but the **column-profile check
  reads the whole table**: it counts nulls and distinct values for up to 100 columns. On an engine
  that bills by data scanned, Amazon Athena especially, that is a full scan of every covered table
  every day, so estimate it before switching coverage on, and exclude large tables you don't need
  watched this closely. Deleting the column-profile check from a suite stops it for that table for
  good. Once a week each covered table is also profiled for suggested rules. A connection covers at most 500 tables (`AUTO_COVERAGE_MAX_ASSETS`); past
  that, the first 500 by name are covered and the overflow is logged.
- **Who can see them.** Automatic suites have no human owner. Workspace admins see all of them
  and can share them; everyone sees the asset's health, which includes them.

### Flat files: formats and CSV delimiters

Flat-file connections (ADLS Gen2 / S3) read `.csv`, `.parquet`/`.pq`, and JSON as
`.jsonl`/`.ndjson`/`.json` (see [JSON files](#json-files) below). The run reads the
format from the file's **extension**; a target's *File format* choice is used by the
profiler and column listing. **The CSV
delimiter is detected per file, not per connection** — a connection is a whole
bucket/container and the files under it need not agree, so DataQ sniffs each file's
header. Comma, semicolon, tab, and pipe are recognised; anything it can't decide
(a single-column file, an empty file) is read as comma-separated.

If a file uses some other separator, DataQ will parse the whole header as one
column — the symptom is a **column dropdown offering a single long name** like
`id;email;amount`. Convert the file to one of the four separators, or to Parquet.

#### JSON files

DataQ reads two JSON shapes, told apart by the file's first character, whatever the
extension:

- **JSON Lines** (NDJSON) — one object per line. The usual shape for event and log
  exports, and the one to prefer: it streams, so sampling and counting never hold the
  file.
- **A top-level array of objects** — `[{...}, {...}]`, compact or pretty-printed.

Each object is one row and each key a column. What is refused, with a message naming
the problem rather than a generic read failure:

- **Nested values.** A column holding an object or an array (`"address": {...}`,
  `"tags": [...]`) has no single scalar type for a check to run against, so the file is
  refused and the columns named. Flatten upstream — `address_city`, not
  `address.city`. Counting rows (a volume monitor) still works on such a file; every
  path that reads columns refuses it.
- **A column whose type changes** — a number in one row and a string in another.
  `null` is fine anywhere, and whole numbers mixed with decimals read as decimals.
- An array whose elements are not all objects, a repeated key in one object, a
  pretty-printed object in a `.jsonl` file (JSON Lines means one per line), or text
  that is not UTF-8.

**Types.** Numbers read as `int64` or `double` (an integer column with a `null` stays
an integer), `true`/`false` as `bool`, and **every string as a string** — including
ISO timestamps and dates. JSON has no date type, and the reader underneath would
otherwise guess `timestamp` for `2026-01-01T10:00:00` but not for
`2026-01-01T10:00:00.5`, so a column's type would change with its data. Freshness
parses a timestamp string, exactly as it does for CSV. A number too large for 64
bits reads as a `double` and loses precision — send identifiers that large as strings.

**Sampling a JSON file** (see below) types it from its **first 1 MiB**. The full read
types every row, so the two can only disagree when the early rows are not
representative — a field that first appears later, a column that is `null` early and
holds values later, or whole numbers early and decimals later. A sampled run refuses
such a file rather than silently retyping it mid-stream; turn sampling off, or make the
first rows representative. Column listing, the profiler and schema drift read the same
first block, so a field that first appears deep in a file is not listed there.

### Very large targets: sampling and the scan cap

Snowflake and Iceberg monitors answer from the warehouse or from file metadata, so
size is not a worker concern there. **Unity Catalog is different for the checks that
still need a DataFrame**: the common expectation types (not-null, unique, between,
in-set, length, regex, row-count and more) push down to a Databricks-SQL batch by
default, so the warehouse evaluates them and the worker never materializes the
table — the same "cost scales with the warehouse" shape as Snowflake. A handful of
types (`expect_column_values_to_be_of_type`, suites with a declared sample) still
run against a DataFrame the worker holds, and **flat files always do**, so a big
enough target on either of those paths runs the worker out of memory. Two things
guard that.

**A hard cap, on by default, on the DataFrame path.** Before a run materializes
anything it checks the object's size (flat files), the table's `COUNT(*)` (Unity
Catalog, when the run isn't pushed down) or the current snapshot's row count
(Iceberg, which reads it straight from metadata). Over the cap the run ends
**failed** with a message naming the target, the two numbers, and what to do —
never a half-finished run or a silent hang. Defaults are 128 MiB and 1.5M rows
(3M rows for Iceberg, whose measured ceiling is higher), tuned for the reference
2 GiB worker; an operator can change them
(`RUN_MAX_SCAN_BYTES` / `RUN_MAX_SCAN_ROWS`, and `RUN_MAX_SCAN_ROWS_ICEBERG` where
Iceberg's own measured ceiling differs — see `deploy/README.md`). A pushed-down
Unity Catalog check is not subject to this cap — it has been run against 200M-row
tables with flat worker memory.

**Sampling, opt-in per suite.** Add a `sampling` block to the suite's target and
checks run against a bounded sample instead of the whole dataset:

```json
{ "path": "landing/orders.csv",
  "sampling": { "strategy": "head", "rows": 100000 } }
```

- `head` — the first N rows. Cheapest by far (a bounded read that stops early), but
  **not representative**: files usually arrive ordered by load time, so a head
  sample sees one slice of the key space. Good for a smoke check, not for a
  uniqueness claim.
- `random` — N rows drawn uniformly across the whole dataset. Representative;
  costs one extra cheap pass to learn the population size. Add `"seed": <int>` to
  make a run reproducible — leave it out and each run inspects different rows,
  which is usually what you want from a monitor.

Sampling **replaces** the size cap for that suite (the read is bounded by the
sample), so it is the supported way to check a target that is otherwise too big.

Things it deliberately **refuses** rather than silently allowing:

- **Sampling on Snowflake or Iceberg targets** — Snowflake never loads rows, so a
  sample there would change nothing while labelling every result "sampled".
- **Freshness monitors are never sampled** — a `MAX` over a sample is a *smaller*
  maximum, which would report healthy data as critically stale.
- **Table row-count expectations on a sampled suite** (`expect_table_row_count_*`)
  — against a sample they measure the *sample* and report it as the dataset's
  size, so a healthy 5M-row file with `min_value: 4000000` would fail critically
  forever. Refused at author time in both directions (adding the check, and
  turning sampling on under one), and per check at run time for suites that
  predate the gate. Use a **volume monitor** instead: it counts the whole dataset
  without loading it.
- **Sampling on a comparison check's `source`** — refused at author time
  (422), not silently ignored. A comparison diffs by key, so two independent
  positional samples of the two sides would share almost no keys and report a
  confidently wrong reconciliation; that needs *coherent* key-set sampling,
  a different mechanism this ADR decided not to build (see
  [ADR 0015's amendment](../adr/0015-two-connection-comparison-check-model.md#amendment-2026-09-16-comparison-sources-do-not-support-sampling)).
  For a large comparison, use `COMPARISON_MAX_ROWS`'s fail-fast cap and narrow
  the source (or target) with a query filter instead of sampling it.

On Unity Catalog a seeded random sample is pushed down as
`TABLESAMPLE (p PERCENT) REPEATABLE (seed)`, so the seed genuinely pins the draw
rather than only being recorded.

**Every result says whether it was sampled**, and only when it genuinely was — a
sample larger than the dataset covered everything, and is reported as a complete
read. Within one run a volume monitor (pushed down, exact) and a sampled
expectation can sit side by side, so the label is per check, not per run.

## Author a check

1. Create (or open) a **suite** and point it at a **target** — a table (Snowflake/UC/PostgreSQL), a
   file/path or batch pattern (ADLS/S3), or an Iceberg `namespace.table`. On every datasource
   you can **browse** for it instead of typing it — see below.
2. **Add check** opens a dedicated page (`/suites/<id>/checks/new`): pick a **category**,
   then the check type, then fill its config. The authoring paths:

### Browsing for a run target

The suite form offers a picker beside the target fields on every datasource; typing the
target still works everywhere.

- **Unity Catalog — Browse catalog…** lists catalogs, then the chosen catalog's schemas,
  then that schema's tables; picking a table fills **Catalog**, **Schema** and **Table**.
  The names come from `system.information_schema`, so the list is what the connection's
  credential can see — a table it has no privilege on is not shown, and an empty level
  means "nothing visible to this credential", not "nothing exists". The `system`,
  `samples` and `__databricks_internal` catalogs are never listed.
- **PostgreSQL — Browse schemas…** lists the schemas the user has `USAGE` on, then that
  schema's tables and views it can `SELECT` — the same query the inventory sync enumerates
  with. There is no catalog level: the connection pins one database. **MySQL / MariaDB**
  works the same way, its schemas being the databases the user has privileges on, and
  **Trino** inside the connection's catalog, listing what the cluster's access control
  lets the user see. **SQL Server / Azure SQL** likewise, inside the connection's database.
- **Snowflake — Browse schemas…** lists the schemas of the connection's database, then a
  schema's tables and views, from that database's `INFORMATION_SCHEMA` — already filtered
  to what the connection's role can see.
- **Iceberg — Browse schemas…** lists the catalog's top-level namespaces, then a namespace's
  tables, and fills **Namespace** and **Table**. It reads catalog metadata only, never a data
  file. Nested namespaces are not listed — type a nested one in.
- **ADLS Gen2 / S3 — Browse files…** (single-file mode) walks the folders of the
  connection's one container or bucket and fills **File path** with the file you pick.
  **Browse folders…** (batch mode) fills **Prefix** with the folder you are in.

At the table level a tag marks anything that is not a plain table: **View**,
**Materialized view**, **Dynamic table** (Snowflake) or **Streaming table** (Unity
Catalog). A freshness or volume monitor on a plain view re-runs the view's query on every
check rather than reading stored rows.

Each level is **one bounded request** of up to 200 names. When a level holds more, the
picker says so ("Showing the first 200 … there are more") rather than presenting a partial
list as the whole thing — type the name into the field instead. A name DataQ cannot target
(anything that is not a plain identifier: letters, digits, `_` and `$`, not starting with a
digit) is listed but not pickable, since a suite pointed at it could not run.

Browsing opens the datasource with the connection's stored credential, so it needs the
**Member** role (the same bar as testing a connection); Viewers cannot author suites and do
not get the picker. Only names, sizes and timestamps come back — never a credential or a
row. The same listings are available over the REST API as
`GET /connections/{id}/browse/catalog` and `GET /connections/{id}/browse/files`.

### GX expectation (all datasources)

Pick a *Column values* / *Table shape* expectation (e.g. `Column values not null`), name
it, set the column, and optionally band severity with **Warn ≥ / Fail ≥ / Critical ≥**
thresholds over the unexpected-%. Leave thresholds blank for binary pass/fail.

**Tolerance (`mostly`).** Most row-wise expectations take an optional tolerance — a
*fraction*, so `0.95` means "pass if at least 95% of rows conform". Leave it blank to
require every row. It moves the line at which the check itself succeeds; it does **not**
change the unexpected-% the severity bands read, so a warn threshold below your tolerance
can still raise a warning on a run the check passed.

**Negative rules.** Several types assert what must *not* be there: *Column values not in
set* (forbidden values, placeholders like `N/A`), *Column values do not match regex*, and
*Column values match none of a list of regexes*. *Column values null* is the inverse of
*not null* — for a deprecated column that must stay empty.

**Beyond one column.** *Compound columns unique* takes a list of columns and checks the
combination (a multi-column key); *Column A greater than column B* compares two columns
row by row, optionally allowing equality; *Column A equals column B* asserts they agree;
*Columns sum to a total* checks that several columns add up per row; *Values unique within
each row* asserts the listed columns differ **within** a row (a transfer whose source and
destination must not match) — as opposed to across rows. *Column distinct values in set* /
*contain set* compare the set of values the column holds rather than counting rows — so
they report **which** values are unexpected or missing, and they have no unexpected-% for
the severity bands to read (thresholds on those two are ignored; the result is a plain
pass/fail).

**Text shape.** *Column value lengths equal* pins a fixed-width code; *Column values match
a list of regexes* accepts several legitimate formats at once (any one of them by default,
or all of them).

**Date formats and JSON.** *Column values match a date format* validates a date or
timestamp stored as text against a Python `strftime` format, and *Column values are valid
JSON* parses a text payload column. Great Expectations implements both for dataframe
batches only, so they are offered on flat files, Iceberg and Unity Catalog but **not on
Snowflake**, where the editor hides them and the API rejects them — use a custom-SQL check
(or a VARIANT column) there rather than saving a check that would error on every run.

### Which expectation types are available

The complete, generated list — every type with its parameters, thresholds and the datasources
it runs on — is the [Check types reference](../reference/check-types.md).

DataQ serves a **vetted subset** of Great Expectations' built-ins, not all of them
(`backend/app/datasources/expectation_allowlist.py`). Every type in it is executed on both
a dataframe and a SQL batch in CI, so it is known to run rather than merely to exist. The
API, the MCP tools and suite import all validate against that same list, so a check written
outside the editor cannot smuggle in a type the editor would not offer; the refusal says
whether the type is unknown to Great Expectations altogether or simply not enabled here,
and lists what is.

Two groups are deliberately absent. **GX's scalar aggregates**
(`expect_column_mean_to_be_between` and its siblings) report a single number and no
unexpected-%, so severity bands have nothing to band — the *Aggregate* monitor measures that
shape properly, with two-sided bands and a trend. **Whole-table set comparisons** (columns match an expected set or
ordered list) are what the *Schema-drift* monitor does, against a captured baseline. For
anything with no vetted type, write a custom-SQL check.

### Custom SQL (Snowflake / Unity Catalog / PostgreSQL / MySQL / Trino / SQL Server / Athena / Redshift — ADR 0019)

A read-only SQL rule in the Monaco editor: **any rows returned are failures**. Use
`{batch}` as a placeholder for the suite's target table
(`SELECT * FROM {batch} WHERE amount < 0`). Single read-only statement enforced
server-side.

The query runs **in the warehouse**, so the result is a pass/fail plus the number
of rows returned — no severity banding (a row count isn't comparable across tables).

**On Unity Catalog the suite's run target must name a schema.** Custom SQL is the
one check kind that needs it: the query is addressed as `catalog.schema.table`, and
a two-part name would silently resolve against the session's default schema — a
*different table*, quietly checked. A UC target without a schema therefore errors
its custom-SQL checks (with that reason on the result) while every other check in
the suite runs normally. Set the schema on the suite's run target to fix it.
Snowflake, PostgreSQL, MySQL and Trino are unaffected — their schema comes from the connection.

### Snowflake DMF (ADR 0036)

On a Snowflake connection, the check editor offers a separate **Snowflake DMF**
category: your own custom DMFs (below), and seven system types — null count, null percent, duplicate count, unique count,
blank count (VARCHAR columns; empty or space-only strings — not NULLs, and tabs/newlines
aren't treated as blank), future-timestamp percent (DATE / TIMESTAMP_LTZ /
TIMESTAMP_TZ columns) and accepted values (below) — that run on Snowflake's own `SNOWFLAKE.CORE.*` **Data Metric Functions** instead of a
GX expectation. Same authoring flow (pick the type, set the column); the difference
is the `engine` the check runs on (`dmf` vs the default `gx`). Every type except unique
count needs a fail or critical threshold, banded like any other metric. Not offered on other
datasources.

**Accepted values (DMF)** counts rows whose value is not in your list, using Snowflake's
`ACCEPTED_VALUES` function. `ACCEPTED_VALUES` can't be called on demand, so DataQ evaluates it
through `SYSTEM$DATA_METRIC_SCAN`. That needs no DMF attached to the table and only the SELECT
rights DataQ already has. **NULLs are not counted as violations.** The scan reads the column
in full.

`SCHEMA_CHANGE_COUNT` is not offered. It only exists as a DMF attached to the table on a
schedule, because it counts changes since its previous scheduled run. Attaching one needs table
ownership, which DataQ's read-only role deliberately lacks. Use the schema-drift monitor
instead.

**Custom DMF** runs a data metric function your team created in Snowflake with
`CREATE DATA METRIC FUNCTION`. Give it the function's fully qualified name
(`DATABASE.SCHEMA.FUNCTION`) and the suite table's columns, in the order the function's
`TABLE(...)` argument declares them. DataQ calls it the same on-demand way as the system
functions:

```sql
SELECT DATAQ_DB.QUALITY.NEG_AMOUNT(SELECT AMOUNT FROM RETAIL.ORDERS)
```

- **Names are identifiers, never SQL.** Each part of the name and each column must be a
  plain identifier (letters, digits, `_`, `$`). Anything else is refused when you save. An
  all-lower-case name is left unquoted, so Snowflake reads it in upper case; any other name is
  quoted exactly as you typed it.
- **The return value is the metric.** It is banded by the thresholds, higher = worse, and a
  fail or critical threshold is required. It is shown as-is, never masked, so write
  functions that return a count or a percentage, not a data value. The check's dimension is
  left unclassified unless you set one, because DataQ cannot know what the function measures.
- **What the role needs:** `USAGE` on the function and on its database and schema, plus
  `SELECT` on the table. A function the role cannot use fails exactly like one that does not
  exist, and the check's error says so.
- **Picking a function.** Testing the connection lists the custom DMFs its role can use, and
  the editor suggests them. The list is only as fresh as the last test, so re-test after
  creating a function. You can always type a name that isn't listed. Functions in the
  `SNOWFLAKE` database are refused: use their own DMF check types.
- **Preview it first.** DMF checks, custom ones included, can be dry-run from the editor. A
  wrong name, missing grant or mismatched column list shows up there before you save.

### Databricks DQX (ADR 0036)

On a Unity Catalog connection, the check editor offers a **Databricks DQX** category: row
rules evaluated by [Databricks Labs DQX](https://github.com/databrickslabs/dqx) inside your
own workspace. The rules are not null, not empty, not null or empty, in list, in range,
matches regex, not less than and not greater than. Each check reports the number of failing
rows, so it fails on any failing row. Optional thresholds band that count instead.

**Where it runs.** DataQ does not contain or install DQX: it is published under the
Databricks License, which permits use only with Databricks services. When a run includes
DQX checks, DataQ uploads a small notebook to the connection user's workspace folder
(`/Users/<you>/.dataq/`) and submits **one serverless job for all of the run's DQX checks**.
The notebook installs a pinned DQX version and returns only failing-row counts, never row
values. Expect about a minute of job start-up per run. For the same reason a DQX check has
**no dry-run preview**: run the suite to see its result.

**What the connection's token needs:** permission to create workspace files in its own home
folder and to submit serverless jobs, plus `SELECT` on the target table. A rule the
workspace rejects errors only that check. A job that fails errors every DQX check in the run
with a classified reason. Your GX checks are unaffected.

**Values are always literals.** DQX evaluates a bare string argument as a Spark SQL
expression, so DataQ builds every rule itself: columns must be plain identifiers, list values
are sent as quoted literals, and limits must be numbers or ISO dates.

**Stream mode.** Each DQX check has a **Read** setting. **Snapshot** (the default) evaluates
the whole table every run. **Stream** evaluates only the rows appended since the check's last
successful run, which is what you want on a Lakeflow streaming table or any Delta table that
grows by appends. It runs in the same one job per run, on your existing schedule. The job reads
the table as a Delta stream (`readStream` with an `availableNow` trigger), counts failing rows
per rule, and stops once it has caught up.

- **Where the stream resumes.** DataQ records the Delta version the stream reached with each
  result, and the next run starts from there. A run whose result was never saved, for example
  because it was cancelled, is re-read next time instead of skipped. The first run of a stream
  check reads the whole table.
- **What the result says.** The failing-row count and the row count cover only the new rows.
  The result also records the version range it read. A run with no new rows is recorded as
  `skip`, not `pass`.
- **Setup.** Set the connection's **DQX checkpoint volume** (`catalog.schema.volume`). Spark
  needs somewhere to keep a stream checkpoint while the job runs, and serverless compute
  refuses both temporary and DBFS locations. DataQ uses a fresh folder in that volume for each
  run and deletes it afterwards. The token needs `READ VOLUME` and `WRITE VOLUME` on it. A
  stream check on a connection without a volume errors on its own; your other checks still
  run.
- **Limits.**
  - Only appended rows are evaluated. A commit that rewrites existing rows (`UPDATE`,
    `DELETE`, `MERGE`, an overwrite) is skipped, including any rows it inserted. The result
    counts the skipped commits in `change_commits_skipped`. Use snapshot mode on a table that
    is not append-only.
  - If the table was replaced, or its history no longer reaches the recorded version, the
    stream restarts from the whole table and the result says why (`restarted`).
  - Run a stream check more often than the table's `VACUUM` retention (7 days by default).
    Otherwise the files it needs to resume from can be gone and the job fails. To restart a
    stream check from the whole table, run it once in snapshot mode.

### Freshness monitor (all datasources — ADR 0012/0030)

*How stale is the target?* Point it at the load/updated **timestamp column**; the check
measures hours since `MAX(column)` and bands that age with the thresholds. A **fail or
critical threshold is required** — without one, a freshness check could never fail.

**On a flat file (ADLS Gen2 / S3) the timestamp column is optional.** Leave it blank
and the check measures **when the file last landed** (the object's modified time)
instead of the newest timestamp inside it. These catch different failures, and a
landing zone usually wants both:

| Blank column (arrival time) | Named column (in-file `MAX`) |
|---|---|
| Catches **"the producer stopped sending files"** — no new file has arrived. An in-file `MAX` is blind to this: the newest file is old, but its rows look perfectly fresh. | Catches **"files keep arriving but the data in them is stale"** — the pipeline runs, the content doesn't advance. |
| Costs a listing, no data read. | Reads the resolved batch. |

Caveats for the in-file form: a CSV's timestamps are text, so they're parsed — use
**ISO-8601**, since an ambiguous `06/07/2026` follows pandas' day-first inference.
A **numeric** column is refused outright rather than read as an epoch offset, which
would date your data to 1970 and fire critical staleness forever.

### Volume monitor (all datasources — ADR 0012/0030)

*Did the load deliver?* Set the expected **min/max row count**; thresholds optionally
band the % by which the count falls outside the range (a spike can exceed 100%), or
leave them blank for binary in-range pass/fail. On a flat file the count is over the
**resolved batch** — the single file the target's batch pattern selects, not the
whole prefix.

### Aggregate monitor (all datasources — ADR 0012)

*Is this column's statistic where it should be?* Pick one statistic — **mean, median, sum,
standard deviation, min or max** — over one numeric column, and set **two-sided bands**:
*Fail below / Fail above*, optionally a tighter *Warn below / Warn above* inside them and a
wider *Critical below / Critical above* outside them. The bands nest (critical ⊇ fail ⊇ warn,
bounds inclusive) and are checked when you save. An aggregate fails too low as well as too
high, so it takes **no** warn/fail/critical threshold — those band a metric that only gets
worse upward, and the editor hides them.

The statistic itself is stored as the result's metric every run, so the **trend view** plots it
with the bands drawn in, and it is ready for a future anomaly baseline. Semantics match SQL on
every datasource: NULLs are skipped, the standard deviation is the sample one (n − 1), and mean,
median and standard deviation are computed over a double-precision float — several engines
(Databricks, Trino, Athena, Redshift, MySQL/MariaDB) otherwise round a DECIMAL average to a
fixed number of places. An **empty table or an all-NULL column reports error**, never a pass
on a made-up 0 (a standard deviation needs at least two values). A text or date column is an
error too: the monitor needs a numeric column.

Warehouses compute it with one pushdown query (Unity Catalog included); flat files and Iceberg
read the one column into the worker, under the same scan caps as a check. **Median** is exact
wherever it is offered and is **not offered on MySQL/MariaDB, Trino or Athena**, which have
only an approximate percentile — use the mean there, or a custom-SQL check with the engine's
`approx_percentile`. Its DQ dimension is left for you to set, since a statistic can speak to
accuracy, validity or consistency depending on why you wrote it. Under zero-sample mode a
**min or max** result keeps its status but not its value: that value is one cell of the table.

### Schema-drift monitor (all datasources — ADR 0012)

*Did the shape change under you?* Capture a **baseline** column-name/type snapshot,
then each run diffs the live snapshot against it and flags any add / drop /
type-change. Introspection is per-datasource, never a `CheckRunner`/GX pass or a
data scan: `information_schema` for Snowflake/Unity Catalog/PostgreSQL/MySQL/Trino, the Parquet footer (or
a bounded CSV header sample, or a JSON file's first 1 MiB) for ADLS Gen2/S3 flat files, and the loaded table's own
metadata for Iceberg. Re-baseline explicitly once you've reviewed a drift and want
it as the new normal — it is never re-baselined for you.

### Anomaly monitor (Snowflake / Unity Catalog / the generic SQL engines — ADR 0012)

*Is this value abnormal for this dataset?* Where a volume monitor asks "is the row
count inside a range I chose?", the anomaly monitor learns the range: it keeps a
rolling mean/stddev of the target's own **row count** or **freshness age** and bands
each run's **z-score** through the usual warn / fail / critical thresholds. Optional
**seasonality** makes the baseline weekday-aware, so a quiet Sunday isn't an anomaly
just for being smaller than Monday.

Below its `min_points` of history the check reports **skip**, never a fabricated
pass — a baseline that hasn't seen enough runs has no opinion. The per-check
**trend view** overlays the learned baseline band on the metric history so you can
see what "normal" currently means. SQL datasources only: the anomaly executor takes
its own measurement over a live SQL connection, which the natively-computed Iceberg
and flat-file monitor paths don't expose.

**Column profile** is a third target metric that watches every column at once. One
query per run measures each column's **null percentage** and **distinct count**, and
each of those gets its own history. The result names the columns that moved, largest
deviation first, and its metric (the z-score the thresholds read) is the largest one.

- A null rate is flagged whichever way it moves. A distinct count is flagged only when
  it **falls**: a growing table's distinct counts rise every day, but a column that
  collapses from 50 values to 1 is a problem.
- A column that has never had a null is not flagged for one stray null: the spread a
  null rate is measured against is never taken as less than 1 percentage point, and a
  distinct count's never as less than 10% of its mean.
- It measures the first 100 columns in table order and reports how many there are in
  total. A column whose type the engine can't cast to a string (Redshift `GEOMETRY` or `BOOLEAN`), or
  whose cast drops values (Redshift `SUPER` objects), keeps its null rate but gets no
  distinct count, and is listed under `distinct_unavailable`. Every other column is still
  counted.

For example, with `{"target_metric": "column_profile", "window": 8, "min_points": 3}` on
`orders`, three runs build the history. When a load then leaves `customer_email` empty,
the next run reports `customer_email` · `null_pct` · value `100` against a mean of about
`2`, and fails.

### Comparison check (all datasources — ADR 0015)

*Does this dataset reconcile against that one?* A comparison check diffs the suite's
dataset (the **target under test**) against a **baseline** on any other datasource
connection — cross-type and cross-env both work (Snowflake DEV vs Snowflake QA, or
Snowflake vs the flat-file extract it was loaded from). Rows are joined on the key
columns you pick, producing **matched / mismatched / additional-per-side** buckets
with a mismatch-% metric that bands through the normal severity thresholds. Reads
are capped fail-fast (`COMPARISON_MAX_ROWS`), samples are redacted like every other
failing-row surface, and a CSV/XLSX report of the differences is downloadable
on demand (derived at read time, never stored). Either SQL side can use a read-only
query projection instead of a whole table.

### DQ dimension (ADR 0038)

Every check carries a **DQ dimension** — the quality aspect it measures. This is a
third axis, separate from the check *kind* (how it works) and the expectation type
(the specific rule):

| Dimension | Question it answers |
|---|---|
| Accuracy | Does the data match reality / a trusted source? |
| Completeness | Is all the expected data present? |
| Consistency | Do related datasets agree with each other? |
| Integrity | Do relationships between datasets hold? |
| Timeliness | Is the data recent enough? |
| Uniqueness | Are there unexpected duplicates? |
| Validity | Does the data conform to its rules and formats? |

**It is filled in for you.** The editor defaults it from the check type — a
not-null check is Completeness, a freshness monitor is Timeliness — and you can
change it at any time, including long after the check was created. Derivation is a
good guess about intent, not a fact: the same range check is *Validity* when it
bounds a percentage and *Accuracy* when it asserts a reconciled total.

Two dimensions are **never** guessed. **Accuracy** and **Integrity** can't be
inferred from the shape of a rule, and a **custom SQL** check is an arbitrary
predicate with no derivable answer at all — those start blank for you to set.

Leaving it blank is legitimate: the check is recorded as *unclassified* and shows
up as a **coverage gap** rather than being quietly filed under a dimension it
doesn't belong to. That matters because the point of dimensions is coverage —
"this table has no Timeliness checks at all" is the actionable finding, and it
would be a lie if unclassified checks were silently bucketed.

Checks created before this feature landed are unclassified until you next edit
them; they were deliberately not bulk-classified, so a derived guess is never
mistaken for someone's decision.

### Type names for `expect_column_values_to_be_of_type`

Everything here applies equally to its sibling **Column values are of one of several
types** (`expect_column_values_to_be_in_type_list`), whose `type_list` takes the same
vocabulary — one entry per acceptable type.

The **Column values are of type** expectation's `type_` field is the one place the
check editor's "obvious" answer is usually wrong. GX validates it against a
*different* type vocabulary depending on which engine actually runs the check — not
the type your warehouse/catalog shows you:

- **Snowflake** builds a real SQL batch (`SqlAlchemyExecutionEngine`) and string-compares
  `type_` against the **fully-qualified dialect type**, not the short column type. A
  `NUMBER` column reports as `DECIMAL(38, 0)`; `VARCHAR` reports as `VARCHAR(16777216)`.
  Plugging in `NUMBER` or `DECIMAL` alone fails every time.
- **MySQL / MariaDB** build a SQL batch too, but GX compares the SQLAlchemy type *class*
  there, so `type_` is the bare type name: `DECIMAL`, `VARCHAR`, `TINYINT` (a `BOOLEAN`).
- **PostgreSQL** builds the same kind of SQL batch and compares the same way: a
  `numeric(12,2)` column is `NUMERIC(12, 2)`, a `timestamptz` is `TIMESTAMP WITH TIME ZONE`.
- **Trino** compares the same way, in its own type names: `DECIMAL(12, 2)`, `VARCHAR`,
  `TIMESTAMP(6)`, `TIMESTAMP(6) WITH TIME ZONE`, `BIGINT`.
- **Unity Catalog, ADLS Gen2 / S3, and Apache Iceberg** all read the target into a
  pandas DataFrame first (`PandasExecutionEngine`). GX first tries an **exact dtype
  match**; only when the column's dtype is `object` and `type_` isn't
  `object`/`object_`/`O` does it fall back to a **row-wise Python value-type compare**.
  In practice:
  - Numeric columns report numpy dtypes — `int64`, `float64`, `bool`. **Caveat:** an
    integer column containing *any* NULL is upcast to `float64` by the read
    (`pd.read_sql_table` / `pd.read_csv`), so a nullable `BIGINT` reports `float64`,
    not `int64`.
  - String columns on **Unity Catalog and CSV** reads are plain pandas `object` dtype
    (these reads are *not* Arrow-backed). Both `type_: object` (exact dtype match) and
    `type_: str` (row-wise value-type match) pass — pick either.
  - **Parquet, JSON and Iceberg** reads *are* Arrow-backed and can report Arrow-flavored
    dtype names — calibrate from a dry-run rather than assuming the CSV/UC names.
  - **`DATE` columns** on Unity Catalog, Parquet and Iceberg stay dates: use
    `type_: date`, and write date bounds and value sets as dates (`2026-01-02`).
    A bound at midnight (`2026-01-02T00:00:00`) is read as that date; one with a
    time of day has no date equivalent and errors.

| Datasource | Engine | `type_` guidance |
|---|---|---|
| Snowflake | SQL (dialect-native) | `DECIMAL(38, 0)` for `NUMBER`, `VARCHAR(16777216)` for `VARCHAR` |
| PostgreSQL | SQL (dialect-native) | `NUMERIC(12, 2)` for `numeric(12,2)`, `TIMESTAMP WITH TIME ZONE` for `timestamptz`, `TEXT`, `INTEGER` |
| MySQL / MariaDB | SQL (SQLAlchemy type class) | `DECIMAL` for `DECIMAL(12,2)`, `VARCHAR`, `INTEGER`, `TIMESTAMP`, `DATETIME`, `TINYINT` for `BOOLEAN` |
| Trino | SQL (dialect-native) | `DECIMAL(12, 2)`, `VARCHAR` / `VARCHAR(20)`, `TIMESTAMP(6)`, `TIMESTAMP(6) WITH TIME ZONE`, `BIGINT` |
| Amazon Athena | SQL (bare type name) | `DECIMAL`, `TIMESTAMP`, `VARCHAR`, `BIGINT`, `BOOLEAN` |
| Amazon Redshift | SQL (bare type name) | `DECIMAL` (not `NUMERIC`), `VARCHAR`, `BIGINT`, `TIMESTAMP`, `TIMESTAMPTZ`, `DATE`, `BOOLEAN`, `SUPER`, `GEOMETRY`; a `VARBYTE` column reads as `VARCHAR` |
| SQL Server | SQL (SQLAlchemy type name) | `DECIMAL` for `decimal(12,2)`, `INTEGER` for `int`, `NVARCHAR`, `DATETIME2`, `DATETIMEOFFSET`, `BIT` |
| Unity Catalog | pandas DataFrame (not Arrow-backed) | `int64` for non-nullable `BIGINT` (**`float64` if the column contains NULLs**); `object` or `str` for `STRING`; `date` for `DATE` |
| ADLS Gen2 / S3 (CSV) | pandas DataFrame (not Arrow-backed) | `int64`/`float64`/`bool` for numerics (**NULLs upcast integers to `float64`**); `object` or `str` for strings |
| ADLS Gen2 / S3 (Parquet) / Iceberg | pandas DataFrame (Arrow-backed) | Arrow-flavored dtype names — confirm via a dry-run's `observed_value` |
| ADLS Gen2 / S3 (JSON) | pandas DataFrame (Arrow-backed) | Python value type names — `int`, `float`, `str`, `bool` (an integer column with NULLs stays `int`); `int64` / `object` do **not** pass |

**Calibration tip:** don't guess — **dry-run first**, but know where the trail runs
out. On **Snowflake, PostgreSQL, Trino and the Arrow-backed sources** (Parquet/JSON/Iceberg), a failing
result's `observed_value` carries the *exact* string GX expected — copy it into
`type_` and re-run to confirm green. On **Unity Catalog / CSV**, a wrong value-type
guess (e.g. `int64` against a string column) falls to GX's row-wise compare, which
fails with **no observed value at all** — the dry-run preview renders Observed as
"—". If you see that, don't hunt for a magic string: the column is `object` dtype, so
enter `object` or the Python value type name (`str`). The check editor's help text
under the field repeats this per the suite's connection type.

Before saving any of them: **Dry-run** previews pass/fail against live data, and the
**column profiler** (nulls, distinct count, min/max, top values) helps place thresholds.

## Run it

Run a suite **now**, on a **[cron schedule](scheduling.md)**, or **triggered** by a
pipeline (see **[Orchestration](orchestration.md)**). Results land on the **Results**
page and the **Dashboard** (health score + trends); failures alert per the suite's
**[notification config](notifications.md)**.

Severity comes from thresholds banding the observed unexpected-percentage
(warn < fail < critical); see ADR 0005 / 0016 for the model, and
**[Best practices](best-practices.md)** for how to pick the bands.
