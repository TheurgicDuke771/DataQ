# ADR 0053 — Governance catalogs: push quality results, pull column classifications

- **Status:** Proposed
- **Date:** 2026-10-25
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0031](0031-oss-byol-distribution-licensing.md) (no SDK whose licence we cannot ship), [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (asset identity by the OpenLineage naming spec; OpenLineage emission with quality facets; the `LineageProvider` seam this mirrors), [0039](0039-openbao-self-hosted-secret-backend.md) (where the catalog credential lives), [0041](0041-history-audit-strategy.md) (audited configuration), [0049](0049-profiler-pii-classification-source.md) (the classification table and the additive-only rule this extends), [0052](0052-data-contracts-odcs.md) (contracts, a later payload). CONTRIBUTING rule 45 (open standards first).

## Context

Data consumers look for data in a governance catalog — DataHub, OpenMetadata, Collibra — not in
DataQ. Two things are missing between them. First, a check's verdict never reaches the catalog,
so the person choosing a table cannot see that its checks are failing. Second, the catalog often
holds the organisation's authoritative column classifications (a `PII` tag, a glossary term),
and DataQ's redaction ladder cannot read them: it reads warehouse tags (G3), the suite policy and
its own profiler detections (ADR 0049), and nothing else.

DataQ already emits OpenLineage run events with data-quality facets (ADR 0034, dark by default)
and already identifies every asset by the OpenLineage dataset-naming spec. Lineage is pulled
through a `LineageProvider` seam with Marquez as the reference. The catalog work fits beside
those, not inside the datasource or orchestration seams: a catalog is neither a place checks run
nor a pipeline that triggers them.

The OpenMetadata Python SDK is published by Collate under terms we have not been able to clear
for redistribution (ADR 0034 recorded it as licence-blocked), so any OpenMetadata support speaks
its REST API directly. DataHub's REST and GraphQL APIs are Apache-2.0 like the server. Collibra's
APIs are proprietary and there is no instance we can test against.

## Decision

### 1. One `CatalogProvider` seam, configured as a workspace integration

A third integration seam beside `LineageProvider` and the alert publishers: one Admin-configured
catalog per workspace (Admin → Integrations), its credential write-only in the `SecretStore`, its
base URL under the credential-destination re-supply rule (moving the URL needs the credential
again), every change audited. Each provider speaks the catalog's HTTP API with `httpx` — no
vendor SDK. The seam has two capabilities a provider may implement independently:
`publish_results` and `fetch_classifications`.

### 2. Push: one assertion per check, one result per run, off the critical path

After a run reaches a terminal state, a Celery task (not the run itself) publishes, for every
check that produced a result, an assertion on the catalog dataset that matches the suite's asset
and a run result for it: pass / fail (warn and critical map to the provider's nearest), the run
time, the check's name, kind and dimension, `metric_value`, and a link to the run in DataQ.
**Never** `observed_value`, failing samples or anything the redaction ladder could mask: the
catalog is a wider audience than the suite. Delivery retries with backoff; a failure is recorded
on a catalog health signal (the `workspace_health` pattern, alerting on the failing edge) and
never fails or delays the run. Assertion identity is stable per check (`dataq:<check_id>`), so a
re-run updates rather than duplicates.

Dataset matching uses the asset's OpenLineage identity, translated per provider (DataHub URN
platform + name + environment; OpenMetadata FQN), with an optional per-connection override for
the catalog's platform-instance name. An asset with no match is skipped and counted on the health
signal, not guessed.

### 3. Pull: catalog classifications as a new, additive-only source

A daily task (and the Admin's "refresh now") reads, for each monitored asset, the column-level
tags and glossary terms the catalog holds, and writes `sensitive` verdicts into
`asset_column_classifications` with `source = 'catalog'` — the table ADR 0049 built for exactly
this. The mapping is **a fixed, documented convention**, never a per-workspace mapping (the G3
argument: a configurable mapping is an unreviewed security control where one typo un-masks a
column): a tag or term whose name, case-folded, is `pii`, `sensitive`, `confidential`,
`restricted`, or a hierarchical name ending in one of those (`Classification.PII`,
`PII.Sensitive`), is `sensitive`.

It is **additive-only**, like the profiler: a catalog can mask a column DataQ would have shown;
it can never un-mask one (a catalog `public` tag is ignored in phase 1), it never satisfies
fail-closed clearance, and the suite's `identifier_column` does not clear it. On the ladder it
sits above the profiler and below the warehouse's own tags; its redaction reason is
`catalog_tag`. A classification that disappears from the catalog is removed on the next pull
(unlike a profiler detection, which needs an Admin dismissal: the catalog is the authority for
its own tags).

### 4. Providers, in order

1. **DataHub** — the reference. Push through GraphQL `upsertCustomAssertion` +
   `reportAssertionResult` (the external-assertion path that works on open-source DataHub, not
   only DataHub Cloud); pull through the dataset's schema fields' tags and glossary terms.
   Verified against a local DataHub quickstart (Apache-2.0) in the harness.
2. **OpenMetadata** — REST only: a test case per check (`/api/v1/dataQuality/testCases`), results
   by `PUT …/testCaseResult`; pull from `/api/v1/tables/name/{fqn}?fields=columns,tags`. Verified
   against a local OpenMetadata server.
3. **Collibra** — not built until a user names it: proprietary API, no test instance, so it would
   ship unverified. The seam makes it a provider module when the demand comes.

### 5. What stays out

Pushing data contracts (ADR 0052) to the catalog, pulling lineage or ownership from it, writing
DataQ's own classifications back to it, and any per-workspace tag mapping. Each is a later,
separate decision.

## Consequences

- One migration: the `source` value-set on `asset_column_classifications` widens by `catalog`,
  and a `catalog_settings` singleton (provider, base URL, secret pointer, enabled flags, last
  push/pull status). Additive.
- The redaction ladder gains a rung and a reason (`catalog_tag`); every surface that shows
  `redaction_reasons` names it, and the pinned rung tests gain a row.
- A new outbound data flow (results to the catalog) is disclosed in the sub-processor list and
  `GET /admin/deployment`, default off.
- Two providers mean two local verification targets in the harness, both heavy (Kafka/OpenSearch
  for DataHub, Elasticsearch/MySQL for OpenMetadata); they run in a verification window, not in CI.
  CI tests the providers against recorded HTTP exchanges.

## Alternatives considered

- **Rely on the OpenLineage data-quality facets DataQ already emits.** The standard-first answer
  (rule 45), and it may suffice for push where a catalog ingests those facets as assertions. To be
  checked first in the build: if DataHub's OpenLineage ingestion turns them into assertions, push
  becomes configuration of the existing emitter and §2 shrinks to the health signal. It does not
  help pull, which has no standard.
- **Vendor SDKs.** DataHub's is usable, but OpenMetadata's is licence-blocked, and one seam over
  plain HTTP keeps both providers the same shape.
- **Catalog classifications that can also clear a column.** Rejected for phase 1: a catalog is
  edited by more people than a warehouse's tags, and an un-masking source needs the review a
  configurable mapping would bypass.
- **Per-workspace tag mapping.** Rejected for the G3 reason above.
