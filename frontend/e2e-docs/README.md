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

The LLM captures (`admin-llm-settings`, `configure-llm`) read the provider config saved in
the scratch database; the clip's **Test** shows _OK_ only when an OpenAI-compatible server
answers at `http://127.0.0.1:11434/v1` (an Ollama with the configured model), otherwise it
records the failure badge. `incident-evidence` needs the seeded incidents, which the seed
rolls up through the real lifecycle engine.
