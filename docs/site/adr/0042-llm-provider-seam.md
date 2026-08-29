# ADR 0042 — `LLMProvider` seam: outbound LLM, BYO credential, default-off

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** @TheurgicDuke771

Adds a new infrastructure seam in the ADR [0010](0010-provider-agnostic-infrastructure-seams.md) family (`ConnectionAdapter`, `SecretStore`, `OrchestrationProvider`, `LineageProvider`). Design intent originated in [docs/post-v1-dq-intelligence-notes.md](https://github.com/TheurgicDuke771/DataQ/blob/main/docs/post-v1-dq-intelligence-notes.md) §LLM integration model.

## Context

v1 has no outbound-LLM capability. The W7 MCP server (ADR [0008](0008-mcp-server.md)) is the opposite direction — it exposes DataQ's tools *to* external LLM clients; nothing lets DataQ *call* a model. Three planned features share that missing capability: the NL→SQL generator for custom-SQL checks, catalog-constrained check suggestions, and later a root-cause narrative for failed checks. One seam, multiple features — the seam must not encode any single feature's prompt shape.

The deployment model constrains the design harder than the features do. DataQ is single-tenant, customer-deployed BYOL (ADR [0013](0013-marketplace-distribution-and-anti-lock-in.md)): the customer's data must not transit an Anthropic/OpenAI key *we* own (that would silently convert BYOL into hosted SaaS with our bill and our data-processing role), and some customers cannot send even schema off-network. The compliance track already lists `llm_intelligence` as an enumerated-while-disabled external transfer in the deployment posture (G4) — an auditor sees it was considered before it existed.

## Decision

### Seam shape

`backend/app/llm/` — an `LLMProvider` protocol with two operations:

- `complete(prompt, *, system, max_tokens, timeout) -> LLMResult` — plain text.
- `complete_structured(prompt, *, schema, system, max_tokens, timeout) -> dict` — JSON conforming to a caller-supplied JSON schema.

Exactly **two wire implementations**, chosen because together they cover every target in the notes:

| Impl | Speaks | Covers |
|---|---|---|
| `anthropic` | Anthropic Messages API via the official `anthropic` SDK (MIT) | Anthropic first-party (default recommendation) |
| `openai_compatible` | `POST {base_url}/chat/completions` via `httpx` | Azure OpenAI/Foundry, AWS Bedrock (native Chat-Completions since 2026), **any local server — Ollama / vLLM / TGI** |

"Local LLM" is deliberately an *endpoint* impl, not a bundled model server — nothing model-shaped enters the image (ADR [0025](0025-production-image-pip-slim.md)). Bedrock's SigV4-only legacy path and Vertex are out of scope until a customer needs them; both now front OpenAI-compatible endpoints.

**Structured output is a capability ladder, not an assumption.** Config carries `structured_output: native | prompt_json`:
- `native` — `response_format: json_schema` (OpenAI-compat) / tool-schema forcing (Anthropic).
- `prompt_json` — schema embedded in the prompt, response parsed + validated, one repair round-trip on parse failure. Exists because small local models (and Ollama's OpenAI-compat parity) do schema-constrained output imperfectly.

Regardless of mode, **the caller always re-validates the parsed output** — the ladder is a reliability feature, never a trust boundary.

### Configuration: DB singleton, admin-only, credential in `SecretStore`

A new `llm_settings` single-row table: `provider`, `base_url`, `model`, `api_key_secret_ref`, `structured_output`, `enabled`, timestamps. Managed via `GET/PUT /api/v1/admin/llm` + `POST /api/v1/admin/llm/test`, all behind `require_workspace_admin` (ADR [0033](0033-workspace-roles-rbac.md)). DB-stored rather than env because it is runtime-mutable workspace config with a test-before-enable flow, not deploy-time infrastructure — the nearest precedent is `SuiteNotification`'s secret-ref columns, not `Settings`.

- The API key is **write-only**: stored via `secret_store.set()` under a minted `llm-provider-<hex>` ref, never returned by any read surface, and the ref itself is server-owned.
- **The credential-redirect rule from connection editing applies:** `base_url` is the credential's destination field. Changing `base_url` (or `provider`) without re-supplying the API key is refused — to point a stored credential somewhere new you must already hold it. Empty-string credentials are refused (`min_length=1`).
- Per-user keys: deferred. Single-tenant team tool; one workspace credential is the model.

### Default off, additive, fail-soft

With no row (or `enabled=false`) every LLM feature is absent, and everything else works — hand authoring, catalog, custom SQL. Feature endpoints return a distinct `llm_not_configured` error the UI renders as "ask your admin", not a 500. A configured-but-unreachable provider is an error *state on the invocation*, never a crash of the calling surface, and — per the ADR 0039 lesson — an outage is reported as an outage (its own error type), never folded into "not configured".

### Execution: Celery worker, polled through `llm_invocations`

LLM calls run **worker-side**, not on the request path. The synchronous alternative (the profiler/dry-run precedent — plain `def`, threadpool) was considered and rejected: BYO endpoints put latency outside our control (a local 7B model can take 30–60s+), which is uvicorn-threadpool starvation under exactly the multi-user load an admin just enabled the feature for.

A new `llm_invocations` table carries the round-trip **and is simultaneously the audit/cost record**: `id`, `kind` (`sql_generation | check_suggestion | …`), `status` (`pending | running | succeeded | failed`), `requested_by`, `suite_id`, `context_fingerprint`, `response` (JSONB), `error`, `input_tokens`/`output_tokens`, `duration_ms`, timestamps. Flow: feature endpoint (suite-`edit`-gated) inserts a row + dispatches → worker calls the provider → UI polls `GET /llm/invocations/{id}`. One table answers "what left the building, when, sent by whom, costing what" — the G4 posture row reads from it.

- Rate limiting: a new `llm` limiter class (ADR [0035](0035-request-rate-limiting.md)) on the feature endpoints — LLM calls are orders of magnitude more expensive than any other request class. A per-workspace daily budget is a recorded follow-up, not built.
- Stored prompts/responses join the retention sweep like other operational rows.

### Data discipline: schema + aggregates only, allowlist out

Prompt context is assembled by one shared builder with a closed vocabulary: **table/column names, types, and aggregate profiler stats** (null %, distinct counts, min/max/top-values) — **never raw sample rows**, and for columns the suite's `column_policy` masks, the value-bearing stats (`top_values`, `min`, `max`) are excluded exactly as the profiler API masks them (reuse `live_probe` masking, don't re-derive). The local-endpoint impl exists for customers who can't send even schema out.

**Trust the LLM's output no more than the user's input.** Generated SQL goes through the ADR [0019](0019-custom-sql-check-kind.md) validator + dry-run; suggested checks through the server-side `expectation_allowlist` + the same config validation as a hand-authored check. Warehouse-controlled strings entering prompts are a prompt-injection surface with its own adversarial battery; the output gates are the security boundary, prompt hygiene is defense-in-depth.

### Explicitly rejected

- **Proxying through a vendor key DataQ owns** — converts BYOL to hosted SaaS (contra ADR 0013).
- **Bundling a model server / weights in the image** — contra ADR 0025; Ollama et al. are the customer's infrastructure.
- **Exposing LLM features as MCP tools** — an MCP client *is* an LLM with its own context; `list_columns` + `dryrun_check` already serve that path better than a second model in the loop. Revisit only on demand.
- **A per-feature provider matrix** (different model per feature) — one workspace provider; per-feature overrides are complexity without a requester.

## Consequences

- The SQL generator and suggestion features (and a future RCA narrative) build feature-shaped prompt builders + output gates on a stable seam; none of them touch provider wire code.
- Live verification (the driver-boundary rule): mocked transports encode our model of a provider — an opt-in lane against a real local inference server (Ollama) is the evidence for the OpenAI-compat impl.
- The posture surface flips `llm_intelligence.enabled` by reading `llm_settings` — the disclosure stays honest in both states.
- New dependency: `anthropic` (MIT) in `backend/requirements.txt`. The OpenAI-compat impl deliberately uses `httpx` directly — no `openai` SDK dependency for a wire format three lines of httpx cover.
