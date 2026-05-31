"""Orchestration providers (ADF, Airflow) — monitor + trigger, never datasources.

Per CLAUDE.md §4, orchestration providers are NOT datasources: a provider
exposes connection config + a connectivity ``test`` (the `ConnectionAdapter`
seam in ``datasources/base.py``) but never a `CheckRunner` — you cannot write a
DQ check against ADF or Airflow. Their runtime role (webhook receipt,
pipeline-run monitoring, suite triggering) lands here in later Week-2 work.
"""
