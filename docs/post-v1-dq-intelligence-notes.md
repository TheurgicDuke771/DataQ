# Post-v1 notes — Expectation expansion, marketplace & LLM-assisted authoring (deferred design)

> **Status: deferred to post-v1.** Captured so the design intent isn't lost. None of
> this is a v1 blocker — the v1 DQ loop (checks → results → trends → freshness/volume
> monitors → alerts → MCP) stands on its own. These are the "intelligence" layer on
> top of it.
>
> **The big enabler (already true in v1):** there is **no server-side expectation
> allowlist** — [`gx_runner`](datasources/gx_runner.py) title-cases *any*
> `expectation_type` string into a GX class, and `checks.config` is free-form JSONB.
> So "add an expectation" / "wire in a check" is mostly a **frontend-catalog + config-
> validation** problem, not a backend-engine one. That's why the items below are
> cheaper than they look.

## The four themes

### 1. Implement the 5 high-ROI GX built-ins
**Feasibility: High · Effort: S–M · No new infra.**

- **4 of 5 are catalog-only** entries in
  [`frontend/src/components/checks/expectationCatalog.ts`](../frontend/src/components/checks/expectationCatalog.ts):
  - **`mostly`** — tolerance on any existing expectation ("95% not-null"). One optional
    field; GX still emits `unexpected_percent`, so it bands under ADR 0016 unchanged.
    **Highest ROI / lowest cost** — do first.
  - **Compound / cross-column** — `expect_compound_columns_to_be_unique` (multi-column
    primary keys), `expect_column_pair_values_a_to_be_greater_than_b` (e.g.
    `end_date > start_date`), `expect_multicolumn_sum_to_equal`.
  - **Type** — `expect_column_values_to_be_of_type` / `in_type_list`.
  - **Set relations / date format** — `expect_column_distinct_values_to_be_in_set` /
    `to_contain_set`, `expect_column_values_to_match_strftime_format`.
- **The 5th — aggregate stats** (`expect_column_{mean,median,sum,stdev,min,max}_to_be_between`)
  is the **design decision, not a free add.** They don't emit `unexpected_percent`; the
  result is a **scalar in `observed_value`** and they're **two-sided** (too low *or* too
  high fails). That's the *volume-monitor* shape, not the GX unexpected-% shape — so
  aggregates likely belong on the **monitor-kind `metric_value` path** (ADR 0012, the
  freshness/volume engine, #426) as a 3rd monitor kind, **not** as a GX-banded
  expectation. Decide deliberately before building.

### 2. LLM SQL generator for custom-SQL checks (`UnexpectedRowsExpectation`)
**Feasibility: High · Effort: M · Best-aligned of the four.**

- NL rule → LLM-generated SQL → existing **custom-SQL guardrails** (ADR 0019:
  read-only single-statement validation + SQL-datasource gating + least-privilege role)
  → **dry-run preview** before save.
- **Principle: trust LLM SQL no more than user SQL** — run it through the *same*
  validator + dry-run. That neutralizes "LLM writes `DROP TABLE`" for free.
- Real risk is *correctness* (hallucinated columns), mitigated by feeding **schema +
  column-profiler stats** as context (we already have the profiler).

### 3. LLM curated check suggestions for a suite
**Feasibility: High · Effort: M.**

- "GX Data Assistant, but LLM-driven and constrained to *our* catalog vocabulary." The
  input it needs already exists — the **column profiler** (null %, distinct counts,
  distributions).
- Must use **structured / schema-constrained output**: the LLM emits checks in the
  catalog's exact `expectation_type` + config schema (derive a tool schema from the
  catalog), so it can't suggest something the runner can't run. Pairs with #1 (richer
  catalog ⇒ better suggestions) and the MCP work.

### 4. Plug-and-play expectation marketplace — **scope = 4a (curated superset)**
**Feasibility: High.**

- **Chosen (4a): a vetted, server-served superset of GX's ~50 built-ins** — the existing
  catalog pattern scaled up. The frontend picks; the backend already runs it.
- **Not doing (4b): an open marketplace** of community/contrib or arbitrary custom
  `Expectation` classes. "Backend wires it in" from user input = a **code-execution /
  supply-chain risk**; such classes would have to be installed in the image + pinned +
  reviewed, not loaded dynamically. Against the anti-supply-chain posture.
- **Useful irony:** doing 4a *safely* means **adding the server-side allowlist** v1
  lacks — flip the "no allowlist" convenience into a vetted catalog the backend validates
  submitted `expectation_type`s against. (This also hardens #2/#3's generated output.)

## Sequencing

```
#1 built-ins ──► richer vocabulary ──► #3 suggestions
   (cheap, now-ish)                        ▲
                                           │ both need:
#4a curated marketplace (extends #1)       │
                              LLM-client seam (NEW) ──► #2 SQL-gen
```

Suggested order: **#1** (immediate value, no infra, unblocks #3's vocabulary) → **#4a**
(extends #1; adds the validation allowlist) → **LLM seam + #2** (highest user "wow,"
guardrails already done) → **#3** (needs the seam *and* the richer catalog).

## LLM integration model (proposed — not locked)

**#2 and #3 share one capability v1 doesn't have: DataQ calling an LLM *outbound*.**
(Note the W7 **MCP server is the opposite direction** — it exposes *our* tools to
external LLM clients; it does not let us call one.) The recommended shape mirrors the
seams the app already has (`ConnectionAdapter`, `SecretStore`, `OrchestrationProvider`):

**An `LLMProvider` seam — admin-configured, default-OFF, customer brings their own
credential/endpoint.**

- **Configuration scope = workspace-admin, not per-user.** Single-tenant + customer-
  deployed (BYOL, ADR 0013): the admin (`WORKSPACE_ADMIN_EMAILS`) configures **one**
  provider (provider + endpoint + model + credential), credential stored in the
  **`SecretStore`** (Key Vault is one impl). Per-user keys are friction for a team tool —
  defer as an optional power-user *override*, not the primary model.
- **Default off + graceful degradation.** The app must fully work with no LLM configured
  (hand-authored checks, the catalog, custom SQL). LLM features are additive.
- **Pluggable concrete impls behind the seam (anti-lock-in, ADR 0010/0013 — no hardcoded
  vendor in business logic):**
  1. **Anthropic API** — default impl (CLAUDE.md: default to the latest Claude models).
  2. **Azure AI Foundry / Azure OpenAI** — for customers already on Azure.
  3. **AWS Bedrock** — for customers on AWS.
  4. **Any OpenAI-compatible endpoint** — this is the **"local LLM" answer**: we do
     **not** bake a model server or HF download into the app image (against the slim-image
     ADR 0025 — GPU/ops/size). Instead the customer runs their own inference server
     (**Ollama / vLLM / HF TGI**, which is where they'd pull an HF model) and points
     DataQ at its OpenAI-compatible URL. "Local LLM" = an endpoint impl, uniform with the
     others.
  - Most of these speak OpenAI-compatible or a thin adapter, so the seam is largely
    `{base_url, api_key, model, auth_adapter}` + a capability flag for tool-use/structured
    output (some local models do JSON-schema output poorly → prompt-based JSON fallback).
- **Cost ownership = the customer's.** BYOL means the customer pays their own LLM bill via
  their own key/endpoint. **Never proxy through an Anthropic key we own** — that turns it
  into hosted SaaS + our cost (contra ADR 0013).
- **Data residency is the deciding axis** for *which* provider a customer picks, and it's
  a hard requirement: apply the **same PII discipline** as the rest of the app — send the
  LLM **schema + aggregate profiler stats only, never raw sample rows / PII-column
  values** (reuse the redaction seam). The local-endpoint impl exists precisely for
  customers who can't send even schema externally.
- **Where it runs:** the **Celery worker** (async, already holds secret access) — not the
  request path. An ADR should record the seam + default impl + the "BYO credential,
  default-off, no-proxy" posture.

## Guiding principle

Build the intelligence layer **on top of** a solid DQ loop, constrained to the catalog
vocabulary and the existing safety seams (custom-SQL read-only validation, PII redaction,
SecretStore, anti-lock-in). The LLM is an **authoring assistant**, never an unchecked
execution path — generated SQL and suggested checks go through the **same** validation a
human's would.
