# DataQ — the story ledger

> Internal document (outside `docs/site/`, never published). Curated narrative
> accounts of moments that prove what DataQ is — kept as raw material for future
> case studies, marketing copy, conference talks, and onboarding. Unlike
> [progress.md](progress.md) (exhaustive, per-PR) this is **selective**: a story
> earns a place by carrying a lesson or a proof, not by being work that happened.
> Add sparingly; each entry is situation → what happened → why it matters.
> When real *user* stories exist, they join here first and graduate to the
> public site only with permission and review.

---

## The four-minute failure (2026-07-02)

A deliberately-broken pipeline (`pl_dataq_smoke_fail`) was fired in Azure Data
Factory against the live deployment. **4 minutes 14 seconds** later the failure
was visible in DataQ — Azure Monitor alert → webhook → immediate-poll ingest,
end to end, measured not claimed. This is the trigger-on-pipeline-event
architecture doing the thing cron-based checkers structurally cannot: the
platform noticed *because the pipeline told it*, not because a schedule happened
to fire. The number is the story — it's reproducible, and it's the demo.

## The bug three green suites couldn't see (2026-08-09)

A pre-deploy QA pass with fresh users and real datasources found that a
legitimately-failing Snowflake NUMERIC check **crashed result persistence** —
`decimal.Decimal` had no branch in the JSON sanitizer, so the whole run's
results were silently discarded (#1273). A *passing* check on the same column
never triggers it, which is why months of green test suites and live smoke runs
sailed past. The lesson that became a standing rule: a value that crosses a
driver boundary is only ever proven by a live run — fixtures encode your model
of the driver, not the driver.

## The feature that had never worked (2026-07-19)

Live verification of Unity Catalog freshness produced its first-ever reading —
**239 hours** — and thereby proved the feature had never worked since it
shipped: the Databricks connector returns a timestamp's MAX as a *string*, and
the age math accepted only datetime (#953). The feature matrix said ✅; every
unit test handed in a real datetime; three green suites, zero readings, no
error anywhere. Found only because the practice is to verify against the
running artifact. "Merged" is not "deployed", and "deployed" is not "works."

## Two clouds, one codebase, five days (2026-08-15/16)

The AWS deployment — ECS, RDS, ElastiCache, Cognito, Secrets Manager,
CloudFront — went from zero to a live, sign-in-verified, suite-running
deployment in days, not months, because every Azure dependency already sat
behind a seam (ADR 0010): auth behind a generic OIDC contract, secrets behind
`SecretStore`, alerts behind `ResultPublisher`, telemetry behind vendor-neutral
OTel. The same week's hardening pass then proved the discipline transfers: the
worker-never-started incident (#1361) was caught because verification checks
per-service image SHAs, never the workflow's exit code.

## The sign-up door nobody knew was open (2026-08-16)

A security audit of the live deployments found the Cognito pool served a
working public **signup form** while the app provisioned an account for any
token the issuer vouched for — anyone on the internet could self-register into
the workspace (#1386). It was killed the same day at both layers (IdP setting +
an app-side allowlist checked per-request, so it revokes as well as admits) —
and then the code review caught a **third door**: the MCP resolver had its own
copy of the auth path, which would have kept accepting what REST now refused.
The transferable lesson: gates live in one shared function per door class, or
they drift.

## An hour of LLM features, a year of seams (2026-08-29)

The v1.2 W3 track — provider seam, admin config, NL→SQL generation with a
validator gate, evidence-card UI, a live-Ollama test lane — merged in a day,
two weeks ahead of schedule, because the `LLMProvider` seam was designed before
any feature needed it and every output rides the same validation a human's SQL
does. The generated check is constrained to be *runnable and safe by
construction*, not by hope: model output passes the ADR 0019 SQL validator on
the exact bytes stored.

## The honesty pass (2026-08-17)

All 46 MCP tools were audited against one question: *what does this answer look
like to a reader with no UI around it?* ~75 findings, almost none crashes —
partial-as-final, unknown-as-zero, one null meaning two things. After the fixes,
the disclosures **changed the answer on 12 of 15 test requests** and prevented
four confidently-wrong ones — including a model declining a destructive action
*because the docstring told it the delete cascades*. The thesis, proven on our
own surface: for AI-facing interfaces, honesty about blind spots is a feature
with measurable effect, not documentation.

## The write probe (2026-08-28)

Minutes after a routine deploy, the first authenticated **write** against
production 500'd: the new audit-chain seal (an UPDATE) collided with the
append-only REVOKE shipped weeks earlier — a conflict the entire test suite was
structurally blind to, because tests run as a superuser that bypasses REVOKE
(#1621). Mitigated live on both clouds the same session, and the post-deploy
checklist gained a permanent line: smoke tests read; **verification writes**.

---

*Add the next story above this line. Selectivity is the point — if every week
adds one, this file has failed at its job.*
