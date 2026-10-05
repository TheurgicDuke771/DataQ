# dataq-client

Python client and command line for the [DataQ](https://github.com/TheurgicDuke771/DataQ) REST
API. Trigger a suite, wait for it, and gate a CI job or a scheduled task on the result, from
Python or from a shell.

```bash
# A released version: the wheel attached to the DataQ release matching your server's version
VERSION=1.2.0   # your DataQ server's version
pip install "https://github.com/TheurgicDuke771/DataQ/releases/download/v$VERSION/dataq_client-$VERSION-py3-none-any.whl"

# The latest from main
pip install "git+https://github.com/TheurgicDuke771/DataQ.git@main#subdirectory=packages/dataq-client"

# The latest from main, editable (to track or change the client locally)
git clone https://github.com/TheurgicDuke771/DataQ.git && pip install -e DataQ/packages/dataq-client
```

It is not published to PyPI. Each [DataQ release](https://github.com/TheurgicDuke771/DataQ/releases) from 1.2.0 on carries the client's
wheel and sdist as release assets; pick the one matching your server.

Authenticate with a personal access token (`dq_live_…`, minted in DataQ under Profile → API
keys). The token acts as its owning user, with the same workspace role and per-suite grants.

## Command line

```bash
export DATAQ_URL=https://dataq.example.com   # the host that serves the DataQ web app
export DATAQ_PAT=dq_live_...                 # from your CI secret store, never a flag

dataq run <suite-id> --wait        # queue, wait, exit by the result
dataq gate --provider airflow --pipeline <dag-id> --env prod --run-id <pipeline-run-id>
dataq export <suite-id> -o orders.yaml
dataq import orders.yaml --connection <connection-id>
dataq validate orders.yaml --connection <connection-id>   # creates nothing; exit 2 if invalid
dataq drift orders.yaml --suite <suite-id>                # writes nothing; exit 2 on drift
dataq apply orders.yaml --suite <suite-id>
```

`dataq run --wait` and `dataq wait <run-id>` exit with (`dataq gate` maps its verdict onto the
same codes: passed 0, failed 2, error 3):

| Code | Meaning |
|---|---|
| 0 | The run finished and nothing failed |
| 1 | The worst result was a warning |
| 2 | A check failed (`fail` or `critical`) |
| 3 | The run itself did not complete (failed or cancelled), or a check could not be evaluated |
| 4 | A client, auth or transport problem, or a timeout; DataQ's verdict is unknown |

A CI gate is one step:

```yaml
- run: |
    pip install "https://github.com/TheurgicDuke771/DataQ/releases/download/v$DATAQ_VERSION/dataq_client-$DATAQ_VERSION-py3-none-any.whl"
    dataq run "$SUITE_ID" --wait
  env:
    DATAQ_URL: ${{ vars.DATAQ_URL }}
    DATAQ_PAT: ${{ secrets.DATAQ_PAT }}
    DATAQ_VERSION: 1.2.0   # your server's version
```

## Python

```python
from dataq_client import DataQClient

with DataQClient() as dq:                      # DATAQ_URL / DATAQ_PAT, or pass them in
    run = dq.trigger_run(suite_id)
    outcome = dq.wait_for_run(run.run_id, timeout=1800)
    print(outcome.status, outcome.worst_severity)
```

`RunOutcome` keeps two things apart that a single "passed" flag would blur:

- **`status`** is the run's lifecycle: `succeeded`, `failed` or `cancelled` once finished.
- **`worst_severity`** is the worst check result: `warn`, `fail`, `critical`, or `None` when
  nothing failed.

A run can succeed with a critical failure, and fail with no results at all. `wait_for_run`
decides "finished" from `status` alone, never from check counts, which describe a partial run
while it is still going. It polls at least every 5 seconds, backing off to 30. A rate-limit
response (429) is raised as `RateLimitedError`, never retried in a tight loop.

## The whole API

`DataQClient` covers the CI and notebook workflows: run, wait, the pipeline gate, export,
import, validate and apply, and acknowledging or resolving an incident. Everything else in the REST API is in the generated
layer, typed from DataQ's OpenAPI spec:

```python
from dataq_client.generated.api.suites import list_suites

suites = list_suites.sync(client=dq.api)
```

Connection and credential routes are reachable only there, never through a convenience helper.

## Versions

`dataq-client` is released with DataQ, and version `1.x.y` is generated from server `1.x.y`.
Use the client version that matches your server; see DataQ's API compatibility policy for what
stays stable across upgrades.

MIT licensed.
