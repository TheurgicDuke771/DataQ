# REST API

DataQ's REST API is the same surface the web UI uses — versioned under `/api/v1`, with the
same per-suite authorization. Use it for scripting, CI integration, or your own tooling.

## Base URL & auth

The API has no public ingress of its own; reach it **through the frontend host**, which
proxies `/api` same-origin (ADR 0028 §5):

```
https://<your-frontend-host>/api/v1/...
```

Authenticate with a **personal access token** (mint one in the UI → Profile → API keys, see
[API keys](api-keys.md)) as a Bearer token:

```bash
BASE=https://<your-frontend-host>/api/v1
TOKEN=dq_live_xxxxxxxx   # keep it out of shell history / source

curl -s -H "Authorization: Bearer $TOKEN" $BASE/me
```

A PAT acts **as its owning user** — every call is scoped by the same suite view/edit rules as
the UI. Unauthenticated requests get `401`. (The interactive Swagger/OpenAPI docs are disabled
in production, #170 — this page is the reference.)

## Conventions

- **Versioning:** all endpoints are under `/api/v1`.
- **Errors:** a JSON envelope — `{"error": {"code": "...", "message": "...", "detail": {...}}}` —
  with a conventional HTTP status (`401` auth, `403` forbidden, `404` not found / hidden,
  `422` validation, `409` conflict, `502` datasource unreachable).
- **IDs** are UUIDs. Timestamps are ISO-8601 UTC.

## Endpoints

### Identity

| Method | Path | What |
|---|---|---|
| GET | `/me` | The current user + `is_workspace_admin`. |
| POST | `/me/api-keys` | Mint a PAT (plaintext returned **once**). |
| GET | `/me/api-keys` | List your keys (metadata only, never the token). |
| DELETE | `/me/api-keys/{id}` | Revoke a key. |

### Connections

| Method | Path | What |
|---|---|---|
| GET / POST | `/connections` | List / create a connection. |
| GET / PATCH / DELETE | `/connections/{id}` | Read / update / delete. |
| POST | `/connections/{id}/test` | Test live connectivity. |
| POST | `/connections/{id}/reauth` | Rotate the credential and verify. |

### Suites & checks

| Method | Path | What |
|---|---|---|
| GET / POST | `/suites` | List / create a suite. |
| GET / PATCH / DELETE | `/suites/{id}` | Read / update / delete. |
| GET / POST | `/suites/{id}/checks` | List / add checks. |
| PATCH / DELETE | `/suites/{id}/checks/{cid}` | Update / delete a check. |
| POST | `/suites/{id}/checks/dryrun` | Preview a check against live data (no persistence). |
| POST | `/suites/{id}/checks/{cid}/snooze` · DELETE to clear | Snooze a check's alerts for N hours. |
| GET | `/suites/{id}/export` · POST `/suites/import` | Portable suite document (env promotion). |
| GET / PUT | `/suites/{id}/column-policy` | Read / set the failing-sample redaction policy. |
| POST | `/suites/{id}/profile` | Column profiler (no persistence). |

### Running & results

| Method | Path | What |
|---|---|---|
| POST | `/suites/{id}/run` | Trigger a run (returns a run id to poll). |
| GET | `/runs` · `/runs/{id}` | List runs / get a run with its results. |
| GET | `/runs/{id}/progress` | Live per-check progress. |
| POST | `/runs/{id}/cancel` | Cancel a queued/running run. |
| GET | `/dashboard/summary` | KPIs + run trend + per-suite performance. |

### Scheduling & orchestration

| Method | Path | What |
|---|---|---|
| GET / POST | `/schedules` | List / create cron schedules. |
| PATCH / DELETE | `/schedules/{id}` | Update / delete. |
| GET | `/pipeline_runs` · `/orchestration/pipelines` | Monitored orchestrator runs. |
| POST | `/orchestration/events/{provider}` | Inbound webhook (adf / airflow / dbt) — authenticated by shared-secret / HMAC, not a PAT. |

## Example: trigger a suite and poll it

```bash
# find the suite id
curl -s -H "Authorization: Bearer $TOKEN" $BASE/suites | jq '.[] | {id, name}'

# trigger a run
RUN=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/suites/$SUITE_ID/run | jq -r .id)

# poll progress until terminal
curl -s -H "Authorization: Bearer $TOKEN" $BASE/runs/$RUN/progress | jq '{status, completed_checks, total_checks}'
```

Prefer natural language? The same actions are available to AI assistants over
[MCP](mcp-setup.md).
