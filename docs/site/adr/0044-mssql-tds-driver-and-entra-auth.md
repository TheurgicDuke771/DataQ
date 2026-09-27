# ADR 0044 — MSSQL / T-SQL adapter: TDS driver choice and Entra service-principal auth

- **Status:** Proposed
- **Date:** 2026-09-27
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (one engine-generic adapter, never an Azure-branded one), [0025](0025-production-image-pip-slim.md) (slim, apt-free runtime image), [0031](0031-oss-byol-distribution-licensing.md) + CONTRIBUTING rule 40 (MIT distribution, dependency licence guardrail), [0036](0036-connection-anchored-check-engines.md) (GX is the engine every connection offers), [0039](0039-openbao-self-hosted-secret-backend.md) (credentials live in the `SecretStore`). The planned OneLake flat-file extension reuses the service-principal credential shape below.

## Context

The Theme-8 MSSQL row adds one `mssql` datasource type covering everything that speaks SQL Server's TDS protocol on port 1433: SQL Server itself, Azure SQL Database, Synapse dedicated pools, and the three Microsoft Fabric SQL items (Warehouse, Lakehouse SQL analytics endpoint, Fabric SQL database). Two constraints decide the design.

**Fabric accepts Microsoft Entra ID only.** Fabric's Warehouse and SQL analytics endpoint take Entra users and service principals; SQL database in Fabric states outright that SQL authentication isn't supported. So the adapter must present an Entra access token over TDS (the FEDAUTH login feature). Plain SQL Server still needs username and password.

**The obvious driver is proprietary.** Every Microsoft Python path to SQL Server ends in the Microsoft ODBC Driver 18 binaries, which are under the *Microsoft Software License Terms*, not an open-source licence. GX 1.17.2's own `add_sql_server` / `add_fabric` builders accept only `mssql+pyodbc` URLs and hand Entra login to that driver (`Authentication=ActiveDirectoryServicePrincipal`). Using them means putting the driver in the image we publish to GHCR.

The options were compared on five criteria: licence against MIT distribution and rule 40, Entra service-principal support, the multi-arch image (linux/amd64 + arm64, apt-free runtime per ADR 0025), GX + SQLAlchemy compatibility, and maintenance.

| | (a) `pyodbc` + msodbcsql18 | (b) `pymssql` | (c) `mssql-python` | (d) `python-tds` + `sqlalchemy-pytds` |
|---|---|---|---|---|
| Python package licence | MIT | LGPL-2.1 (weak copyleft, OK with notice) | MIT | MIT + MIT |
| Native driver licence | **Proprietary EULA** (apt `msodbcsql18`, `ACCEPT_EULA=Y`) | FreeTDS, statically linked in the wheel (LGPL) | **Proprietary EULA** — hard dependency `mssql-python-odbc`, classified *Other/Proprietary*, which carries the same ODBC 18 binaries | none (pure Python) |
| Entra service-principal token | yes (driver-native) | **no** — FreeTDS has no FEDAUTH (the upstream feature request has been open since 2023) | yes (driver-native) | yes — `access_token_callable` sends a FEDAUTH security token |
| Reaches Fabric at all | yes | **no** | yes | yes on paper; unproven live |
| Image impact | apt repo + `unixodbc` + `msodbcsql18` in the runtime stage (breaks "apt-free") | wheel only | ~27 MB + ~4 MB wheels per arch, plus apt `libltdl7 libkrb5-3 libgssapi-krb5-2` | ~95 KB of wheels; TLS via `pyOpenSSL`, already installed as a transitive dependency |
| SQLAlchemy / GX | `mssql+pyodbc`, GX first-class builders | `mssql+pymssql` (GX `add_sql` only) | `mssql+mssqlpython` exists only in SQLAlchemy **2.1** (DataQ pins 2.0.54; GX 1.17.2 on 2.1 unverified) | `mssql+pytds`; the dialect subclasses SQLAlchemy's `MSDialect`, so GX sees dialect `mssql`; GX `add_sql` only |
| Maintenance | Microsoft, GA | active (2.4.2, 2026-09) | Microsoft, GA | `python-tds` 1.17.1 (2025-09), one maintainer; `sqlalchemy-pytds` 1.0.2 (2024-09), one maintainer, *Beta*, ~360 lines |

**What the ODBC 18 EULA asks of a redistributor** (read from the licence text itself, not a summary; this is not legal advice): §1(a) grants use "to develop and test your applications", and anything beyond that depends on the distributable-code clause. §2(a)(i) makes only code *on the REDIST list* distributable, and I could not find a REDIST list that names the Linux `msodbcsql18` package. §2(b)(ii) requires that "distributors and external end users … agree to terms that protect it and Microsoft at least as much as this agreement". §2(b)(iii) requires the distributor to "indemnify, defend, and hold harmless Microsoft". §4(e) forbids distributing the software except as distributable code. The pass-through and indemnity obligations would attach to anyone who pulls the GHCR image, which cannot be squared with ADR 0031's plain MIT, no-strings distribution. The rule-40 list names copyleft and source-available licences; a closed proprietary binary is further from MIT than either of those, so shipping it would need its own ADR-level exception.

**Common practice** is to accept the EULA in the image build, as the Bitnami Airflow image does behind an `INSTALL_MSSQL_CLIENT` switch. Microsoft now ships the same ODBC 18 binaries through PyPI in `mssql-python-odbc`. That shows Microsoft tolerates redistribution. It does not remove the obligations above from the party that redistributes.

## Decision (proposed — the user decides)

1. **Driver: `python-tds` + `sqlalchemy-pytds` (option d), gated on a live spike.** Both are MIT and pure Python. Neither needs an apt step, so the image stays apt-free and multi-arch with no per-arch work. The Entra access token is sent over TDS FEDAUTH. There is no proprietary component and no rule-40 exception. GX runs through the generic `context.data_sources.add_sql(connection_string="mssql+pytds://…", kwargs={"connect_args": …})`, not the pyodbc-only `add_sql_server` / `add_fabric` builders. DataQ classifies connection errors itself, as it already does for every other type.
2. **The spike gate is mandatory, and it is live** (standing rule: for anything that crosses a driver boundary, only a live run counts). Before any adapter code merges, the spike must pass all of:
   - Azure SQL Database, with both SQL authentication and a service-principal token, and TLS certificate validation on.
   - A Fabric Warehouse **and** a Lakehouse SQL analytics endpoint, both with a service-principal token.
   - Azure SQL's *Redirect* connection policy, which is the default for clients inside Azure such as our Container Apps. `python-tds` implements TDS routing, but that path has not been tested here.
   - Representative GX work through `mssql+pytds`: `UnexpectedRowsExpectation` (custom SQL), a column expectation, the freshness and volume monitors, and a profiler pass.
   - A negative certificate test: a wrong hostname and an untrusted CA must both be refused.

   If any item fails and cannot be fixed upstream or in a small wrapper, fall back to **option (a)** under a new ADR that records the rule-40 exception, the EULA obligations, and counsel sign-off. Option (c) replaces (a) once DataQ is on SQLAlchemy 2.1.
3. **Guard the known driver sharp edges in the adapter:**
   - `python-tds` only enables TLS when it is given a `cafile`. The adapter therefore always passes the `certifi` bundle, or a configured private-CA bundle, and has no code path that connects unencrypted.
   - TLS is 1.2-only through `pyOpenSSL`, which Azure SQL and Fabric accept. TDS 8 *strict* encryption is not supported; record that as a limitation.
   - Hostname verification is hand-rolled in `pytds/tls.py`. It compares exactly and case-sensitively, handles a leading-label wildcard, and strips the `DNS:` prefix with `lstrip`, which removes a character *set* rather than a prefix and can wrongly reject valid names. Pin the version and cover the matcher with the negative test in item 2.
   - `pyOpenSSL` becomes a direct pin, because the adapter now depends on it directly.
   - `sqlalchemy-pytds` is small enough to vendor if it goes unmaintained.
4. **Auth model — one `mssql` type, two auth modes, one secret each.**
   - **`auth_type: sql`**: `username` is config and the password is the secret. For SQL Server, Azure SQL, and Synapse SQL logins.
   - **`auth_type: entra_service_principal`**: `tenant_id` and `client_id` are config and the client secret is the secret. This is the same shape as the ADF connection (`orchestration/adf.py`), so nothing new goes into the `SecretStore` model. The token comes from `azure-identity`'s `ClientSecretCredential`, already a runtime dependency, for scope `https://database.windows.net/.default`, which covers both Azure SQL and Fabric SQL. The adapter passes `lambda: credential.get_token(scope).token` as `access_token_callable`, which `python-tds` calls once per login, so every new physical connection asks the credential for a token. `azure-identity` caches tokens and refreshes them before they expire, so there is no refresh code of our own. An established session outlives its login token. Engines are built per run, the way the Snowflake and UC runners build theirs, so no pooled connection outlives a run by long.
   - **Deferred:** managed identity (the same deferral ADLS already carries), certificate credentials, and sovereign-cloud authority hosts.
   - **Credential-destination rule** (a config change that moves where a credential is sent requires re-supplying it): `destination_fields = {"secret": ("host", "port", "tenant_id", "auth_type")}`. The SQL password travels to `host`. The client secret travels to the token endpoint of `tenant_id`, and the resulting token travels to `host`. Switching `auth_type` would re-purpose one stored secret as another kind of credential. Any of these changes therefore requires re-supplying the secret.
   - **Fabric prerequisites stay the customer's job:** the Fabric tenant setting *Service principals can use Fabric APIs*, and a workspace role or item permission for the service principal. The Test Connection path should name these when the login fails.
5. **The adapter is engine-generic** (ADR 0010/0013). It has one `mssql` type with no Fabric- or Azure-branded variant. The dialect is `mssql`, and T-SQL differences surface as capabilities. For example, Fabric Warehouse has no `TABLESAMPLE` and limited `INFORMATION_SCHEMA` metadata, and the spike determines the sampling and `enumerate_tables` behaviour for each item.

## Consequences

- No proprietary binary in the published image, and rule 40 holds without an exception, provided the spike passes.
- Adapter work is more than the `ConnectionAdapter` alone. It also touches: the `CheckRunner` on the shared `gx_runner`; `monitors.py`, where Core `Select` quoting is already dialect-driven; the `tsql` dialect in the custom-SQL validator; sampling; `enumerate_tables` (ADR 0040) over `INFORMATION_SCHEMA`; the profiler; the connection-spec UI; and the per-connection-type value sets in a migration. It should be split into issues once the spike passes.
- Supply-chain risk moves to two single-maintainer packages. Mitigations: exact pins, the rule-39 quarterly audit, and the option to vendor the dialect.
- Pure-Python TDS is slower than ODBC on large row transfers. DataQ pushes aggregates down and caps samples, so the spike should measure this but it is not expected to decide anything.
- **Test harness:** SQL Server Developer and Express images are proprietary EULA, which the harness rule (no commercial licence, even in the harness) keeps out. The Azure SQL Database free offer is a hosted service. It runs no licensed binary on our side and costs nothing within its monthly allowance, so it avoids that problem. It does tie the live lane to Azure. For Azure-free CI of the SQL-auth path, **Babelfish for PostgreSQL** (Apache-2.0, speaks TDS on 1433) is worth a follow-up spike. It cannot stand in for Entra or Fabric.

## Alternatives considered

- **(a) `pyodbc` + msodbcsql18 in the published image.** The most proven path, first-class in GX, and the one Microsoft documents for Fabric. Rejected as the *default* for the licence reasons above. It remains the documented fallback if the spike fails, under its own ADR.
- **(a′) Keep `pyodbc` but make msodbcsql18 bring-your-own**, installed in a derived image the operator builds. This keeps our artifact clean, but the MSSQL type would be dead in the image we publish, and the eval stack could not demonstrate it. Keep it as an escape hatch, not as the design.
- **(b) `pymssql`.** Its licence is acceptable, but it has no Entra token support, so it cannot reach Fabric at all. Rejected.
- **(c) `mssql-python`.** Microsoft's modern driver, GA, and handles Entra natively. Its hard dependency on the proprietary `mssql-python-odbc` gives it the same licence problem as (a), delivered through pip, where the rule-40 licence sweep would flag it as *Other/Proprietary*. Its SQLAlchemy dialect also needs SQLAlchemy 2.1. Rejected for now. It is the preferred successor to (a) if a proprietary driver is ever accepted.
- **Separate `fabric` and `azure_sql` connection types.** Rejected by ADR 0010/0013: all of them are the same protocol with different auth.
