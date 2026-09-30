# DataQ ↔ Azure Data Factory — pipeline gate

`dataq_gate_pipeline.json` is an example pipeline showing the three activities that make an ADF
pipeline wait for DataQ's verdict and fail itself when the data fails (DataQ ADR 0046). Copy
the activities between the stage that lands data and the stages that consume it.

1. **Bind the pipeline** in DataQ (Suite → Triggers) to the suites that should gate it, with
   the pipeline's name and env.
2. **Create a PAT** for a DataQ user with **edit** on every bound suite, and store it in your
   Key Vault as `dataq-gate-pat`. Grant the factory's managed identity *get* on that secret.
3. **Set the parameters:** `dataqUrl` (your DataQ host), `dataqEnv` (the binding's env) and
   optionally `failOn` (`warn`, `fail` or `critical`).

How it works: an **Until** loop POSTs to `/api/v1/orchestration/gate` with the pipeline's name
and `RunId`. The first request starts the bound suites' runs for this pipeline run, and every
later request is the same idempotent call, so DataQ runs them once. When the gate answers
`passed`, `failed` or `error`, the loop ends, and anything but `passed` hits a **Fail** activity,
so the downstream stages never run. The Web activities set secure input and output under their
`policy` (ADF ignores the flags anywhere else), so the token never lands in the run history. If
you deployed an earlier copy with the flags outside `policy`, redeploy and rotate the PAT:
earlier runs keep it in their history until retention expires. The loop gives up after 30
minutes.

To gate a *downstream* pipeline on an upstream one's run instead, send `"trigger": false` and
the upstream run's pipeline name and run id; the gate then only reports on the runs that the
upstream success event triggered.
