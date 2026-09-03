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

Conventions: 1440×900 viewport at 2× (`deviceScaleFactor`), light theme, one test per
image, image names are stable (docs pages reference them by name). Only demo identities
appear — the pre-commit identifier hook cannot read PNGs, so this lane is the guard.
