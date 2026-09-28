# Python client and CLI

`dataq-client` is a Python package and a `dataq` command for driving DataQ from scripts, CI
pipelines, notebooks and schedulers. Its most common use is gating a deploy on a data-quality
result: trigger a suite, wait for it, and fail the pipeline step if a check failed.

```bash
# A released version: the wheel attached to the DataQ release matching your server's version
VERSION=1.2.0   # your DataQ server's version
pip install "https://github.com/TheurgicDuke771/DataQ/releases/download/v$VERSION/dataq_client-$VERSION-py3-none-any.whl"

# The latest from main
pip install "git+https://github.com/TheurgicDuke771/DataQ.git@main#subdirectory=packages/dataq-client"

# The latest from main, editable (to track or change the client locally)
git clone https://github.com/TheurgicDuke771/DataQ.git && pip install -e DataQ/packages/dataq-client
```

The client is not published to PyPI. Each [DataQ release](https://github.com/TheurgicDuke771/DataQ/releases) from 1.2.0 on carries its wheel
and sdist as release assets; install the one matching your server's version. Until your server's
release carries one, install from `main`.

It authenticates with a [personal access token](api-keys.md), which acts as the user who minted
it, with the same workspace role and per-suite grants.

## Gate a pipeline step on a suite

```bash
export DATAQ_URL=https://dataq.example.com   # the host that serves the DataQ web app
export DATAQ_PAT=dq_live_...                 # from your CI secret store

dataq run 7c1e…-suite-id --wait
```

`--wait` polls the run until it finishes and exits with a code your pipeline can act on:

| Exit code | Meaning |
|---|---|
| 0 | The run finished and nothing failed |
| 1 | The worst result was a **warning** |
| 2 | A check **failed** (`fail` or `critical`) |
| 3 | The run itself did not complete (failed or cancelled), or a check could not be evaluated |
| 4 | A client, authentication or network problem: the result is unknown |

A GitHub Actions step:

```yaml
- name: Data-quality gate
  run: |
    pip install "https://github.com/TheurgicDuke771/DataQ/releases/download/v$DATAQ_VERSION/dataq_client-$DATAQ_VERSION-py3-none-any.whl"
    dataq run "$SUITE_ID" --wait
  env:
    DATAQ_URL: ${{ vars.DATAQ_URL }}
    DATAQ_PAT: ${{ secrets.DATAQ_PAT }}
    DATAQ_VERSION: 1.2.0   # your server's version
    SUITE_ID: 7c1e…
```

The token is read from `DATAQ_PAT` only, never from a command-line flag, where it would land in
shell history and process listings. To treat warnings as a pass, allow exit code 1 in your
pipeline (for example `dataq run … --wait || [ $? -eq 1 ]`).

Other commands:

```bash
dataq wait <run-id>                               # wait for a run someone else started
dataq run <suite-id> --wait --json                # the outcome as JSON
dataq export <suite-id> -o orders.json            # the suite's portable document
dataq import orders.json --connection <conn-id>   # create a suite from it
```

## From Python

```python
from dataq_client import DataQClient

with DataQClient() as dq:            # reads DATAQ_URL / DATAQ_PAT, or pass them in
    run = dq.trigger_run(suite_id)
    outcome = dq.wait_for_run(run.run_id, timeout=1800)

if outcome.status != "succeeded" or outcome.worst_severity in ("fail", "critical"):
    raise SystemExit(f"data quality gate failed: {outcome}")
```

A run's outcome has **two separate answers**, and the client never merges them into one
"passed" flag:

- **`status`** says whether the run itself finished: `succeeded`, `failed` or `cancelled`.
- **`worst_severity`** is the worst check result: `warn`, `fail`, `critical`, or `None` when
  nothing failed.

A run can **succeed** and still report a critical failure: every check ran, and one found bad
data. It can also **fail** with no results at all, when the target could not be read. Check
both.

`wait_for_run` decides a run has finished from its `status` alone. The pass counts of a run
that is still going describe only the checks done so far. It polls every 5 seconds at the
fastest, backing off to 30. When the API rate-limits the token, it raises `RateLimitedError`
instead of retrying in a loop.

## The rest of the API

`DataQClient` covers running, waiting, exporting and importing suites, and acknowledging or
resolving incidents. Every other REST endpoint is available through the generated layer, typed
from the API's own specification:

```python
from dataq_client.generated.api.suites import list_suites

for suite in list_suites.sync(client=dq.api) or []:
    print(suite.id, suite.name)
```

## Versions

The client is released with DataQ: client `1.x.y` is generated from server `1.x.y`. Use the
client version that matches your server. [API compatibility](../reference/api-compatibility.md)
describes what stays stable across upgrades.
