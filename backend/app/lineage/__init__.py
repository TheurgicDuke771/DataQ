"""OpenLineage emission (ADR 0034, gap G-d, #758).

Emits START / COMPLETE / FAIL / ABORT ``RunEvent``s for suite runs, carrying the
run's target asset as an input ``Dataset`` with data-quality facets. **Dark by
default** — with no OpenLineage transport configured in the environment, nothing
here constructs a client, imports an openlineage transport, or emits anything (the
library's own console-default must never activate on an unconfigured deployment).

- ``emitter`` — the env-gated cached client + the pure ``RunEvent`` builders.
- ``dispatch`` — the fail-open choke point the worker calls (loads the run graph,
  builds, emits; never raises, never slows a run beyond the emit call itself).
"""
