# Docs capture lane (screenshots + short videos)

Produces the images under `docs/site/assets/screenshots/` — never hand-taken, so a
UI change is a re-run, not a photo session. Runs against a scratch dev-bypass stack
with the seeded demo data (own database `dataq_docs`, api `:8001`, Vite `:3001`), so
the developer's compose stack is untouched. Never in CI.

```bash
scripts/docs/capture-stack.sh start     # migrate + seed + api + vite
scripts/docs/capture-stack.sh capture   # = E2E_DOCS=1 playwright test --project docs-capture
scripts/docs/capture-stack.sh stop
```

Videos: `videos.spec.ts` records ≤20 s clips at 1× (test title = file name);
`scripts/docs/transcode-videos.sh` turns the webm into an H.264 mp4 (~100–300 KB) plus a
poster jpg under `docs/site/assets/videos/`. Embed with `<video class="clip" autoplay loop
muted playsinline>`.

Conventions: 1440×900 viewport at 2× (`deviceScaleFactor`), light theme, one test per
image, image names are stable (docs pages reference them by name). Only demo identities
appear — the pre-commit identifier hook cannot read PNGs, so this lane is the guard.

The LLM captures (`admin-llm-settings`, `configure-llm`) read the provider config that
`capture-stack.sh start` saves through the API after the seed (`DOCS_LLM_MODEL` /
`DOCS_LLM_BASE_URL` override the Ollama defaults). The clip's **Test** shows _OK_ only when an
OpenAI-compatible server answers at that base URL; otherwise it records the named failure
badge. `incident-evidence` needs the seeded incidents, which the seed rolls up through the
real lifecycle engine.

The SQL-generation and check-suggestion captures (`sql-generate-result`, `suggest-checks`,
the `generate-sql`/`suggest-checks` clips) need a **live** SQL connection — both features
introspect the target table for real, so a fake config errors on every call. `start` creates
one, opt-in, when `DOCS_SNOWFLAKE_ACCOUNT` is set (plus `DOCS_SNOWFLAKE_USER` and
`DOCS_SNOWFLAKE_SECRET` — no defaults, deliberately, so no identifier is committed to this
file; the rest default to the harness reader's shape — `DATAQ_DB.RETAIL.ORDERS_HEADER`,
warehouse `DATAQ_WH`, role `DATAQ_READER`). Without them, `start` aborts rather than
silently skipping the live-warehouse captures; leave all three unset to run the base set
with no warehouse credentials at hand.
