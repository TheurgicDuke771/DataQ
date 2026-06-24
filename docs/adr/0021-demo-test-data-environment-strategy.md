# ADR 0021 — Live test/demo-data environment (retail model) lives outside the repo

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0004](0004-orchestration-abstraction.md) (orchestration abstraction — the flows exercise it), [0010](0010-provider-agnostic-infrastructure-seams.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (Azure/Key Vault is one impl behind each seam; no Azure resource names in the repo), [0003](0003-gx-only-for-v1.md) (GX run paths the flows feed), [0012](0012-monitor-kind-seam.md) (the reserved freshness/volume/anomaly/schema-drift kinds the harness shapes data for), [0014](0014-reconciliation-comparison-check-kind.md) (the cross-platform `comparison` target the route map sets up). Discharges the standing **"live warehouse/file run — deferred Week-1 smoke"** caveat carried in CLAUDE.md §13 and `docs/progress.md`.

## Context

Every DataQ run path — Snowflake (`SnowflakeCheckRunner`), flat-file (`FlatFileCheckRunner` + batch resolution), Unity Catalog (`UnityCatalogCheckRunner`) — is unit-tested against canned in-memory DataFrames with the **network seam stubbed** (real GX execution, mocked IO). That is deliberate and correct for CI, but it means no run has ever touched a live warehouse, live ADLS/S3 object, or live UC table. The roadmap has carried this as the *"deferred Week-1 smoke"* since Week 1.

Closing it needs a **realistic, repeatable data environment**: real datasources with believable data, real orchestration picking the data up, and the three DataQ run paths executing against it end-to-end. The data should model a real domain (retail) so checks, profiling, severity tiers and trends look like production, not toy fixtures.

The question this ADR settles is **what, if anything, of that environment belongs in the DataQ source repo.**

## Decision

**The test/demo-data environment is a harness that lives _outside_ the DataQ source repo.** None of the setup code is committed:

- **Terraform** that provisions Azure + Snowflake infra once accounts are enabled — local / separate, **not git-tracked** (also keeps Azure resource names out of the repo, per ADR 0010/0013).
- **Mock-data generators** for the retail model and the seed data they produce — out of repo.
- **The Databricks RAW→Silver→Gold notebook** that processes landed files — out of repo.

What **is** git-tracked is only documentation that points at the harness and records this decision: this ADR, plus the roadmap / `progress.md` pointers that finally give the deferred-smoke caveat an owner.

**Retail data model** the harness seeds (domains → entities): Product (style, colour, material, SKU) · Inventory (on-hand, planned) · Location (store, address, store hours) · Sales (placed, shipped, audited) · Return/Cancellation (return, cancellation, guest, platform) · Logistics (shipment, tracking, carrier) · Guest/Customer (details, survey, chatbot feedback). Plus cross-cutting: on-sale/discount, promotion/award/coupon, tax rate, exchange rate, product price, misc.

> **The detailed, evolving plan lives in the harness tracker, not here.** The fleshed-out data model (e.g. the Orders header↔line-item split, Payments/Finance, Channel as a first-class dimension), the **key-consistency** generation discipline (the same `order_number`/`sku_id`/`tracking_number` across domains *and* across batch loads), the **dataset → run-path route map** (which entity lands on Snowflake vs Unity-Catalog-plain-load vs the Databricks medallion vs flat-file), the **one-system-of-record discipline** (Snowflake `Orders` is the truth; the medallion `gold` aggregate must reconcile back to it — the ADR-0014 `comparison` target), and the **reserved-kind data shaping** (timestamped/incremental feeds for freshness/volume/anomaly, a perturbable feed for schema-drift — ADR 0012) are tracked in the external harness plan. This ADR records the *strategy*; that plan holds the *detail* (which would otherwise drift in a committed doc — consistent with "only the decision is git-tracked" above).

Data is split across **ADLS · Snowflake · Unity Catalog**; orchestration across **ADF · Airflow** (both behind the existing `OrchestrationProvider` abstraction — ADR 0004; no provider-specific branching).

**Three reference flows** the harness drives, each exercising a distinct DataQ run path:

- **Flow A — load to warehouse.** File lands in ADLS → ADF/Airflow picks it up → loads into Snowflake / UC → DataQ runs a suite against the warehouse table (`SnowflakeCheckRunner` / `UnityCatalogCheckRunner`).
- **Flow B — medallion.** File lands in UC RAW → Databricks notebook runs → processes RAW → Silver → Gold → DataQ runs suites against the UC tables.
- **Flow C — flat-file in place.** File lands in ADLS and **stays a flat file** (no warehouse load) → DataQ runs a suite directly against it (`FlatFileCheckRunner` + batch resolution picks the latest batch).

**Hosting — local/fast-iteration profile (chosen).** Airflow runs **locally via the official `apache/airflow` docker-compose** (webserver + scheduler + Postgres metadata DB + optional Celery workers), while **ADLS / Snowflake / Unity Catalog point at real cloud accounts**. This keeps the orchestration loop cheap and fast to iterate while the datasources stay realistic. Terraform is **not** used for the local Airflow (docker-compose is the norm; the `kreuzwerker/docker` provider is unnecessary friction); ADF, being managed, is exercised in its cloud form. A fully-cloud profile (Terraform-provisioned **Azure Managed Airflow** or AKS-hosted, alongside the rest of the infra) is a valid alternative for a more production-like run, but is **not** the v1 default. Either way Airflow stays an `OrchestrationProvider` behind the abstraction (ADR 0004) — DataQ is agnostic to how it is hosted.

## Consequences

**Positive**
- The deferred live-warehouse/file smoke gets a concrete, owned mechanism instead of floating as a caveat.
- The repo stays clean of infra/seed/harness code and Azure-specific resource names (ADR 0010/0013 anti-lock-in holds).
- The three flows give real end-to-end coverage of all three run paths **and** the ADF + Airflow orchestration trigger-on-success path (ADR 0004), against believable retail data.
- Profiler, severity tiers, and Week-6 trends/anomaly baselines get realistic data to render against.
- The harness's parameterizable volume also backs a **performance / scale-testing** pass (post-v1): run scaling per datasource, the profiler N+1 ([#327](https://github.com/TheurgicDuke771/DataQ/issues/327)), and DB-growth API latency ([#323](https://github.com/TheurgicDuke771/DataQ/issues/323)) — baseline-first, tracked in the harness plan, invokable by the QA/QE agent.

**Negative / watch**
- The harness is unversioned alongside the product; its setup steps must be documented well enough to reproduce (README in the external harness location).
- **Boundary to hold:** if any harness artifact later becomes a *generic, user-facing reference template* (the way `integrations/airflow/dataq_airflow_callback.py` is a shipped template, **not** app code), it would move into `integrations/` (e.g. `integrations/databricks/`) and become git-tracked — but the retail-specific harness stays out. This ADR governs the demo harness, not future generic templates.

## Alternatives considered

- **Commit the harness into the repo (e.g. `scripts/mockdata/`, `infra/terraform/`).** Rejected: bloats the product repo with environment-specific seed + Azure resource names, working against the anti-lock-in posture (ADR 0010/0013), and couples product CI to harness churn. Credentials-in-tracked-files risk too (working agreement).
- **Keep relying on canned-frame unit tests only.** Rejected: never discharges the live smoke; profiling/trends keep running on toy data.
- **Ship the Databricks notebook as an `integrations/` template now.** Deferred: it's currently the retail-specific harness, not a generalised user template. Revisit if/when a generic medallion-integration reference is wanted.
