# Session handoff — v1.3 Week 7 batch work

> **What this is.** A save point for the AI-assisted work session that started 2026-11-11. The
> session's container is ephemeral and its usage limit has stopped work twice, so the state that
> matters is written here and pushed. A new session reads this file first, then checks it
> against GitHub (open PRs, branch heads) before acting — the remote is the truth, this file is
> the map. It lives only on the branch `claude/confident-maxwell-g79bbh` and is never merged to
> `main`; delete the branch when the batch is done.
>
> **Last updated:** 2026-11-14 07:30 UTC.

## Standing decisions from the maintainer

- **One branch and one PR per issue** (or per tightly coupled pair), named per CONTRIBUTING
  (`fix/issue-<N>-…`, `feature/…`, `chore/…`, `docs/…`). Stack dependent PRs (rule 9a).
- **Merge without waiting**: run `/code-review` on the PR, post findings (`--comment`), fix or
  file each one, then squash-merge once CI is green (auto-merge is fine).
- **Scope for now: batches A + B + C** below. Check in before starting D (performance),
  E (accessibility) or F (test debt).
- **Issue hygiene**: verify against the code, then close duplicates with evidence.
- Delegating to sub-agents is allowed. `docs/progress.md` is updated once, in a final docs PR,
  not in each fix PR (it would conflict across parallel PRs).

## How to save progress (the process)

1. **Push every branch as soon as it has a commit**, even before its PR is opened. An unpushed
   commit dies with the container.
2. **Update the table below and push this file** at each milestone: a branch committed, a PR
   opened, a review done, a PR merged, a decision taken.
3. **Uncommitted work is not saved.** A lane that is mid-edit when the session dies is redone
   from its issue; the table says which lanes were mid-edit at the last update.
4. On resume: `git fetch origin`, read this file, list open PRs, compare branch heads with the
   SHAs below, then continue from the first row that is not `merged`.

## State

Status values: `todo` · `wip (uncommitted)` · `committed` (pushed, no PR) · `PR open` · `merged`.

### Done

| Issue(s) | PR | Status |
|---|---|---|
| #2533, #2473, #2485, #2526 — dataq-client ruff/mypy pin drift | #2663 | merged 2026-11-11 |
| #2400 — admin guide had two Members sections | #2664 | merged 2026-11-11 |
| #2399 — Stryker config comment pointed at the wrong file | #2665 | merged 2026-11-11 |
| #2401 — README link to third-party notices | #2666 | merged 2026-11-11 |
| #2529 — closed as duplicate of #2531 (fixed by PR #2532), evidence in the issue | — | closed |

### Batch A — security

| Issue | Branch | Head | Status |
|---|---|---|---|
| #2615 contract export shows value sets of a column classified sensitive later | `fix/issue-2615-contract-export-value-sets` | — | wip (uncommitted): code, tests, docs, fragment and `openapi.json` written; gate was running. Needs `origin/main` merged in and the client regenerated |

### Batch B — MCP polish (a stack; each branch is cut from the one above)

| Issue | Branch | Head | Status |
|---|---|---|---|
| #2367 get_suite_performance wording | `fix/issue-2367-mcp-suite-performance-wording` | `1f3f127` | committed |
| #2353 list_runs trigger label | `fix/issue-2353-mcp-list-runs-trigger-label` | `aa38da1` | committed |
| #2360 asset health score on list_assets / get_asset | `feature/issue-2360-mcp-asset-health-score` | `c454e21` | committed |
| #2417 tools do not say a check is switched off | `fix/issue-2417-mcp-disabled-check-flag` | — | wip (uncommitted) |
| #1838 classification as ToolAnnotations; derive GATES and docs | `refactor/issue-1838-mcp-tool-annotations` | — | todo |

Each MCP PR needs the `mcp-honesty-reviewer` agent as well as `/code-review` (rule 44). Open the
PRs bottom-up with `base` set to the parent branch; merge bottom-up, merging `origin/main` into
the next branch after each merge (never rebase).

### Batch C — small backend fixes (independent branches from `main`)

| Issue | Branch | Head | Status |
|---|---|---|---|
| #2354 alerts say 'ADF', the app says 'Azure Data Factory' | `fix/issue-2354-alert-provider-display-name` | `f43cb7c` | committed |
| #2356 SQL-generation prompt lists every column uncapped | `fix/issue-2356-llm-sql-prompt-column-cap` | `95ff6cf` | committed |
| #2235 profiler builds the store client and reads the secret twice | `fix/issue-2235-profiler-single-store-client` | — | wip (uncommitted) |

None of the `committed` branches has been reviewed by the orchestrating session yet: read the
diff, run the slice of tests, then open the PR.

### Not started (needs the maintainer's go-ahead)

- **D — performance:** #1999 then #2117 (scheduler dispatch), #2012, #2010 (likely a two-step
  migration), #1987 (keyset paging, API contract change), #1979, #1597.
- **E — accessibility:** #2067, #2064, #2061, #2051, then the audits #1672 and #1673, which
  gate #2520.
- **F — test debt (Week 8):** #2623 first (root cause of #2358, #2474, #2475), then #2135,
  #2551, #2545, #2573/#2115 (one flake, filed twice), #2608.
- Unscheduled P2s worth a decision: #2643 (runs can stack after an outage), #2638, #2639.
- Final docs PR: refresh the snapshot and tick the rows in `docs/progress.md` (the snapshot
  says 74 open issues; the live count was 85 on 2026-11-11).

## Working in the cloud container

- No conda and no git hooks here. System `python3` (3.13) has `backend/requirements-dev.txt`
  installed; run `python3 -m pytest` (the bare `pytest`/`ruff` on PATH are other installs —
  ruff there is 0.16.9; put `/usr/local/bin` first on PATH for the client generator).
- After a container restart: `pg_ctlcluster 16 main start` and `redis-server --daemonize yes`.
  Role `dataq`/`dataq`; databases `dataq_test`, `dataq_lane_a|b|c` (one per parallel lane).
  If the container was replaced, reinstall (`pip install -r backend/requirements-dev.txt`,
  `pnpm install --frozen-lockfile` in `frontend/`) and recreate the role and databases.
- Test env: `TEST_DATABASE_URL` and `DATABASE_URL` =
  `postgresql+psycopg2://dataq:dataq@localhost:5432/<db>`, `REDIS_URL=redis://localhost:6379/<n>`;
  add `--no-cov` when running a slice.
- CI note: on 2026-11-11 the Playwright E2E job failed on `main` and every PR because the
  runner could not reach the Ubuntu apt mirrors during `playwright install-deps`; a re-run
  hours later passed. If it recurs, check `main` before suspecting the PR.
