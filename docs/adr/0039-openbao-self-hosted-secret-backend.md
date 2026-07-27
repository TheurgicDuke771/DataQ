# ADR 0039 — OpenBao as the self-hosted secret backend (KV v2 API, not a vendor)

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** @TheurgicDuke771

Extends the `SecretStore` seam of ADR [0010](0010-provider-agnostic-infrastructure-seams.md) with a fourth backend; amends nothing — the production Azure Key Vault choice of ADR [0024](0024-app-deployment-infrastructure.md) stands.

## Context

`SecretStore` ([`backend/app/core/secrets.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/core/secrets.py)) has carried three implementations since Week 2:

| Mode | Backend | Used by |
|---|---|---|
| `env` | `KV_SECRET_*` process env vars | host-only dev; **per-process**, so the worker cannot read what the API wrote (#86) |
| `redis` | plaintext keys in the dev Redis | `docker-compose.yml` **and** `docker-compose.ghcr.yml` (the published eval/BYOL stack) |
| `azure_key_vault` | Azure Key Vault via `DefaultAzureCredential` | production (#406/#408) |

Two problems have accumulated behind that table.

**1. The only multi-process store we ship is plaintext.** `RedisSecretStore` exists for a real reason — the API writes a connection credential and the Celery worker must read it (#86), which `EnvSecretStore` structurally cannot do — but it stores warehouse credentials **unencrypted, with no auth, no audit trail, and no TTL**, in the same Redis that serves as the Celery broker. That was scoped as "dev/test only" and the docstring says so. It has since become the configured default of `docker-compose.ghcr.yml`, which is the artifact ADR [0031](0031-oss-byol-distribution-licensing.md) publishes as the free BYOL eval path. An evaluator following our own README puts real Snowflake credentials into a plaintext key-value store, and nothing in the product tells them so.

**2. Production has exactly one backend, and it is Azure.** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) and [0013](0013-marketplace-distribution-and-anti-lock-in.md) commit to Azure being *one implementation behind each seam*. Every other seam honours that — `OrchestrationProvider`, `LineageProvider`, the OTLP exporter, the generic OIDC contract of ADR [0028](0028-cloud-neutral-image-runtime-config-generic-oidc.md). `SecretStore` does not: a customer deploying the OSS into AWS or GCP (#505) has a choice between Azure Key Vault (wrong cloud) and a plaintext Redis key. This is the largest remaining hole in the anti-lock-in posture.

The obvious candidate — HashiCorp Vault — carries a licensing problem. **Vault Community Edition has been BUSL-1.1 since v1.15 (August 2023)**, and CONTRIBUTING rule 40 / ADR [0031](0031-oss-byol-distribution-licensing.md) §4 names BUSL explicitly in the forbidden set. The constraint does not bite uniformly, and the distinction is what makes this decision possible:

- The *client* side is unaffected — talking to an HTTP API creates no licensing relationship, and the KV v2 request/response shapes are not a licensed artifact.
- The *server* side is where it bites. Putting a BUSL image into `docker-compose.yml` or into the BYOL install artifact is distribution of a source-available component, needs an ADR-level exception, and drags a non-OSS dependency into the marketplace certification path #732 deliberately kept clean.

**OpenBao** is the Linux Foundation (Edge) fork of Vault taken at the pre-BUSL 1.14 point, licensed **MPL-2.0** — weak copyleft, which ADR 0031 §4 permits with notice — and wire-compatible with the Vault KV v2 API.

The API surface was verified empirically against `openbao/openbao` **v2.6.1** rather than taken from documentation, and two behaviours turned out to be load-bearing for the design:

```
GET    /v1/secret/data/<name>       → 200, value at .data.data.value
POST   /v1/secret/data/<name>       → 200, body {"data": {"value": "…"}}
DELETE /v1/secret/metadata/<name>   → 204, purges all versions (204 when absent too)
auth header: X-Vault-Token          (OpenBao retains the Vault header name)
dev mode mounts kv-v2 at secret/, listens on :8200
```

- **A missing secret and a soft-deleted secret both return 404** — so a status-code check covers both, and no body inspection is needed to tell them apart.
- **A dead token returns 403 and a sealed vault returns 503** — neither is a missing secret. Collapsing them into "secret not found" would reproduce the exact failure this project has now hit twice: #954, where two dead Snowflake PATs produced no visible state anywhere and had to be root-caused from worker logs, and #828 more generally. A credential store that reports "your credential is missing" when the truth is "I cannot reach the vault" is an invisible-degradation machine.

## Decision

**1. Add a fourth `SecretStore` implementation that speaks the KV v2 HTTP API — not a vendor SDK.** `OpenBaoSecretStore` issues three plain `httpx` calls against `/v1/{mount}/data|metadata/{name}`. Because the contract is the API and not a client library, the same mode works unchanged against OpenBao, Vault Community, Vault Enterprise, and HCP Vault. **DataQ supports the API; the operator chooses the server.** This keeps the anti-lock-in property one level higher than it would sit with a vendor client: we are not swapping Azure lock-in for HashiCorp lock-in.

**2. OpenBao is the server DataQ ships, tests, and documents.** MPL-2.0, so CONTRIBUTING rule 40 stays intact with **no exception required** and no source-available component enters the published artifacts. Vault Community is *supported* (same API) but never distributed by us — operators who already run it point `OPENBAO_ADDR` at it. The image is pinned (`openbao/openbao:2.6.1`), not tracked at `latest`, for the same reason the GX and `redis:7` pins exist.

**3. `SECRET_STORE=openbao` replaces `redis`, and `RedisSecretStore` is deleted.** It becomes the default of both `docker-compose.yml` and `docker-compose.ghcr.yml`. This is the point of the change: keeping a plaintext store around as a "convenient" alternative preserves the exact footgun being removed, and a default nobody chose is the one evaluators get.

   **Redis is not going anywhere.** It remains the Celery broker/result backend, the beat scheduler's store, and the rate-limit counter store (ADR [0035](0035-request-rate-limiting.md)). This decision removes one of Redis's two unrelated jobs, not Redis. The local stack gains a container; it does not lose one.

   Removal is made **loud rather than silent**: `SECRET_STORE=redis` stays in the accepted `Literal` for one cycle and raises a `RuntimeError` naming this ADR and the replacement mode. A bare `Literal` rejection would emit a pydantic "Input should be 'env', 'openbao' or 'azure_key_vault'" with no cause and no migration path. Dev/eval secrets held in Redis are **not migrated** — they are throwaway by construction, and re-entering a connection credential is a documented step, not a data-loss event.

   They are, however, **purged rather than abandoned**. Switching modes does not delete anything: the live migration of the dev stack left **1392 `dataq:secret:*` keys** — plaintext warehouse credentials — sitting in Redis, readable by anything with access to the broker, and nothing in the app would ever touch them again. A migration away from plaintext storage that leaves the plaintext in place is half a fix, so the `RuntimeError` carries the purge command (`redis-cli --scan --pattern 'dataq:secret:*' | xargs -r redis-cli del`). It is not run automatically — deleting a user's data on startup is not this code's call.

**4. Token auth in phase 1; AppRole shipped in phase 2 (#1054, 2026-07-27).** `OPENBAO_TOKEN` is sent as `X-Vault-Token`. AppRole (`role_id`/`secret_id` → login → renewable token), which is what a production self-hosted deployment should use, is **deferred and filed**, not silently omitted — see Related.

  **Phase 2 has since landed.** `OPENBAO_ROLE_ID` + `OPENBAO_SECRET_ID` log in at
  `POST /v1/auth/approle/login` and the issued token is cached, renewed proactively
  before its lease ends, and re-acquired once on a 403 — so a revoked or expired
  token self-heals instead of 403ing every request until the process is restarted.
  Renewal happens **at request time, not on a background thread**: a thread would
  need to exist in the API and every worker, would need its own shutdown path, and
  could wedge silently (the #904 shape), whereas a check immediately before use
  cannot wedge without also stopping the requests it guards. Exactly one retry, and
  only in AppRole mode — more would turn a bad `secret_id` into a login storm, and
  in static-token mode a 403 is the operator's answer and must surface unchanged.
  `hvac` was reconsidered here as #1054 suggested and **still declined**: AppRole is
  one more endpoint, and the reason for speaking the wire contract (one mode serving
  OpenBao / Vault / HCP) is unchanged by adding it. A partial AppRole config is
  rejected at startup rather than falling back to the token. The token is never logged: it is not in the request path or query string, and the logger-level redactor (ADR-independent, `backend/app/core/logging.py`) covers `token` keys and bare token shapes. No credential enters a git-tracked file — `scripts/setup.sh` generates the local dev-mode root token into the gitignored `.env`, exactly as it already does for the local Postgres password (CLAUDE.md §11).

**5. Azure Key Vault remains the production default for DataQ's own Azure deployment.** Managed identity means no bootstrap credential at all, which is strictly better than a token DataQ has to hold, and the Terraform stack (ADR 0024) already provisions it. OpenBao is the **cloud-neutral option for BYOL deployments**, not a replacement for Key Vault on Azure. One store implementation covering AWS + GCP + on-prem beats building and maintaining AWS Secrets Manager and GCP Secret Manager adapters separately.

**6. Failure modes stay distinguishable — in the exception TYPE, not the message.** A new `SecretStoreUnavailableError`, **deliberately not a subclass of `SecretNotFoundError`**, is raised whenever the store could not answer: a dead token (403), a sealed or erroring vault (503/5xx), a missing KV mount, a transport failure, a non-JSON 200 (we are not talking to the vault at all), or a KV v1 envelope. `SecretNotFoundError` now means one thing only — the secret genuinely is not there. Every failure also emits a WARNING naming the cause; **no status is silent**, because the 400-499 band is where the likely misconfigurations live (400 = a KV v1 mount, 401 = a gateway swallowing the vault's 403, 429 = HCP rate-limiting under check-run load).

   The type is the load-bearing part, and an earlier draft of this ADR got it wrong. It asserted that "the Protocol only lets us raise `SecretNotFoundError`" and settled for putting the distinction in the message. **The Protocol declares no exceptions at all**, and no caller reads the message — every one of them branches on the class:

   - `admin_service._safe_secret` returns `None`, and the admin webhook page renders **"not set"** — while its own docstring promises "an unexpected error still propagates rather than masquerading as 'not set'"
   - `notification_service._resolve_webhook_url` returns `None` and **alert delivery is silently skipped**, logged as "no webhook configured"
   - `connection_service._extra_secrets` **omits the credential and runs the connection anyway**
   - `alerting/email.py` drops the alert

   So a sealed vault would have read, to the product and to the operator, as "nothing is configured" — across every connection at once, during an outage, with alerting off. That is #954 rebuilt inside the very change meant to end it. `AzureKeyVaultStore` had shipped this defect since Week 2 (it wrapped *every* SDK exception in `SecretNotFoundError`), so a Key Vault throttle or an expired managed identity has been rendering as "not set" in production; both stores are fixed here.

   Per decision 4 of ADR [0034](0034-asset-entity-openlineage-identity-lineage-pull.md)'s reasoning about observations vs outages: an outage must not be reportable as a state.

**7. `delete` purges, and that differs from Key Vault on purpose.** KV v2 `DELETE /data/` soft-deletes only the latest version and leaves the value recoverable; `DELETE /metadata/` removes every version. DataQ calls the **metadata** delete, because the caller is orphan cleanup after a connection is deleted (#372) and leaving a recoverable warehouse credential behind a deleted entity is the wrong default. It stays **fail-soft** (never raises) like every other `delete` on the seam. Note the asymmetry with `AzureKeyVaultStore`, which soft-deletes because Key Vault's purge protection is a vault-level policy, not ours to choose.

## Consequences

**Positive**

- The `SecretStore` seam finally honours ADR 0010: one implementation serves AWS, GCP, on-prem and any Kubernetes deployment, and no cloud's secret manager is privileged in the code.
- The published eval stack stops storing warehouse credentials in plaintext. Encryption at rest, an auth boundary, an audit log and secret versioning arrive with the container.
- No licensing exception, no source-available dependency, no change to the marketplace-certification surface (#732). The dependency tree stays as clean as ADR 0031's audit found it.
- Operators already running Vault (Community or Enterprise) or HCP get support for free via API compatibility, without DataQ taking any position on their edition.
- Removing `RedisSecretStore` removes a class of confusion — Redis's two jobs were entirely unrelated and sharing one instance between a broker and a credential store invited exactly the "we can just put it in Redis" reflex that produced the problem.

**Negative / accepted trade-offs**

- **The local stack grows a fifth service.** `docker compose up` starts one more container and `setup.sh` generates one more token. Accepted: the eval stack is the artifact strangers judge the project by, and it was demonstrating a practice we would reject in review.
- **A bootstrap credential now exists where none did.** Redis mode needed no token. Token auth is phase 1 and is weaker than managed identity — but the thing it replaces is *no auth at all*, so the direction is unambiguous even before AppRole lands.
- **Dev-mode OpenBao is in-memory.** Restarting the container drops every secret and the dev stack must re-enter connection credentials. This is the documented dev-mode trade (it is also what makes it unseal automatically); a persistent local vault would need a storage backend and manual unseal on every boot, which is the wrong trade for a dev stack.
- **`SECRET_STORE=redis` breaks for anyone carrying it in a local `.env.app`.** Deliberate, and made loud per decision 3. One cycle of the shim, then the mode goes.
- **OpenBao is a younger project than Vault** with a smaller operator community, and the fork will drift from Vault over time. The API-not-vendor framing of decision 1 is the hedge: if OpenBao stalls, the same mode still speaks to Vault or HCP and only the shipped image changes.

## Alternatives considered

- **HashiCorp Vault Community Edition** — rejected as a *distributed* component, supported as a *target*. BUSL-1.1 is named in CONTRIBUTING rule 40 / ADR 0031 §4; shipping it in compose or the BYOL artifact would need an ADR exception and would put a source-available image into the marketplace path. The API-level support of decision 1 means users who want Vault lose nothing.
- **Keep `RedisSecretStore` alongside OpenBao** — rejected. The plaintext store is the defect; retaining it as an option retains the defect for everyone who does not read the docstring, and a default nobody chose is what evaluators actually run.
- **AWS Secrets Manager + GCP Secret Manager adapters** — rejected for now, on cost of ownership: two more implementations, two more SDK dependency trees, two more auth models to test, versus one that covers both clouds plus on-prem. Worth revisiting if a customer specifically wants IAM-native secret access without running a vault; nothing in this decision blocks adding them behind the same seam later.
- **`hvac` (the Apache-2.0 Python Vault client)** — rejected. It would add a dependency and a *vendor-shaped* abstraction to make three HTTP calls, when `httpx` is already used for every other outbound HTTP integration in the backend (`marquez.py`, `adf.py`, `airflow.py`, the alerting publishers). The KV v2 surface DataQ needs is small and stable; a client library earns its keep at AppRole/renewal time, which is the point at which to reconsider it.
- **Encrypt values before writing them to Redis** — rejected. It moves the problem rather than solving it: the encryption key then needs a store, which is the original question, and it delivers none of the audit/versioning/access-control that motivates a vault.
- **Infisical / other OSS secret managers** — not pursued. The KV v2 API has the widest compatibility surface (four server implementations honour it), which is precisely the property decision 1 is buying.

## Related

- ADR [0010](0010-provider-agnostic-infrastructure-seams.md) — the provider-agnostic seam rule this closes the last major gap in.
- ADR [0013](0013-marketplace-distribution-and-anti-lock-in.md) / [0031](0031-oss-byol-distribution-licensing.md) — BYOL distribution and the rule-40 license guardrail that rules out Vault Community as a shipped component.
- ADR [0024](0024-app-deployment-infrastructure.md) — the Azure Key Vault production provisioning that stands unchanged.
- ADR [0035](0035-request-rate-limiting.md) — the Redis rate-limit counters that are one of the two reasons Redis stays in the stack.
- #86 — the cross-process requirement that `EnvSecretStore` cannot meet and that motivated `RedisSecretStore` in the first place.
- #372 — the `SecretStore.delete` orphan-cleanup contract decision 7 implements.
- #505 — AWS/GCP deploy IaC; this ADR removes the secret-store blocker from that work.
- #954 / #828 — the invisible-dead-credential failures that decision 6 is written against.
