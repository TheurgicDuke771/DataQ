# Static demo lane

The read-only demo published at `/demo/` on the GitHub Pages site (#2419) is the real
frontend, built with `VITE_DEMO=true` so the API client answers from recorded responses
instead of a backend. Nothing here is committed output: CI records, builds and smoke-tests
it on every run of the `frontend-e2e` job, and a push to `main` publishes the result.

| Step   | Command             | What it does                                                                                                                                                                                     |
| ------ | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Record | `pnpm demo:capture` | Visits every route in `routes.ts` (and each tab on it) against the seeded dev-bypass stack and writes the `GET /api/v1` responses to `demo-fixtures.json`.                                       |
| Build  | `pnpm demo:build`   | Builds the SPA for `DEMO_BASE` (default `/DataQ/demo/`) into `dist-demo/`, with the fixtures and a `config.js` that selects no-IdP mode.                                                         |
| Smoke  | `pnpm demo:smoke`   | Serves `marketing/` + `dist-demo/` the way GitHub Pages does (`pages-server.mjs`) and drives the built files: every route renders from the recording alone, deep links work, writes are refused. |

Locally, record against the docs capture stack so your own workspace is never the source:

```bash
scripts/docs/capture-stack.sh start
cd frontend
E2E_DEMO_BASE_URL=http://127.0.0.1:3001 pnpm demo:capture
pnpm demo:build && pnpm demo:smoke
```

## What keeps it from drifting

- **A new route** must be added to `ROUTE_PATTERNS` in `routes.ts`;
  `tests/demo/routeCoverage.test.ts` fails otherwise.
- **A new request on an existing route** is recorded automatically, because the recording is
  remade from the current code on every CI run.
- **A request the recording cannot answer** (a value that changes between record and replay,
  a call that bypasses the shared axios client, an absolute URL that ignores the base path)
  fails the smoke test.
- **Dates** in the recording are moved forward by whole days at replay, so the data reads as
  recent between publishes.

## What the demo does not do

Requests made only after a click that the crawl does not perform (a drawer, a filter, a
download) are not recorded. The demo says so in a notice instead of showing an empty or
wrong view. Every non-`GET` request is refused with a notice that links to the install
guide.
