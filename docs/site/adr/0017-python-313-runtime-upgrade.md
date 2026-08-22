# ADR 0017 — Upgrade Python runtime 3.11 → 3.13

- **Status:** Accepted
- **Date:** 2026-06-08
- **Deciders:** @TheurgicDuke771
- **Supersedes:** the Week-1 Python-3.11 tooling lock (CLAUDE.md §6 / CONTRIBUTING rule 18)

## Context

The Week-1 tooling lock pinned the runtime to **Python 3.11** ("do not drift"). Two forces made revisiting it worthwhile, together, now:

1. **Security (the trigger).** The full-runtime CVE audit (#130) surfaced 5 advisories in `cryptography` (PYSEC-2026-36/35, CVE-2026-26007) and `pyOpenSSL` (CVE-2026-27448/27459) that **could not be fixed at the existing pins**: `snowflake-connector-python 3.15.0` capped `cffi<2.0` and `pyOpenSSL<26.0.0`, blocking `cryptography≥46.0.7` and `pyOpenSSL≥26`. Moving the connector to **4.x** drops those caps (`4.6.0` requires only `cryptography>=46.0.5`, `pyOpenSSL>=24.0.0`), and `snowflake-sqlalchemy 1.10.0` lifts the old `connector<4.0` cap. See #129.
2. **Runtime longevity.** Python 3.11 reaches security-EOL in Oct 2027. 3.13 (released Oct 2024) is mature and extends the clock to ~Oct 2029.

Because both the connector bump and the Python bump require a **full conda env rebuild + revalidation**, doing them as one effort pays that cost once.

## Decision

**Adopt Python 3.13** as the project runtime, and **not 3.14**.

**Why 3.13 and not 3.14:** Great Expectations is our pinned, sole v1 DQ engine (ADR 0003). GX 1.17 supports Python **3.10–3.13**; **3.14 is experimental-only** (gated behind a `GX_PYTHON_EXPERIMENTAL` install flag). Tracking GX loosely or running it experimentally contradicts the GX-pinning rule, so GX caps the runtime at 3.13 until it ships non-experimental 3.14 support.

Bundled dependency moves (#129): `snowflake-connector-python 3.15.0 → 4.6.0`, `snowflake-sqlalchemy 1.7.4 → 1.10.0`; `cryptography` and `pyOpenSSL` then resolve transitively to CVE-fixed versions (46.0.7 / 26.2.0).

Surfaces changed: `environment.yml` (`python=3.13`), `pyproject.toml` (Black + Ruff `target-version`, mypy `python_version`), `.pre-commit-config.yaml` (Black `language_version`), `.github/workflows/ci.yml` (`python-version` ×5), and the docs (CLAUDE.md / CONTRIBUTING).

## Consequences

**Positive**
- Clears all 5 dependency CVEs (`pip-audit` clean on the rebuilt env).
- Modern runtime with headroom to ~2029; access to 3.12/3.13 language + stdlib improvements.
- Every pinned + transitive dependency (GX 1.17.2, databricks-sql-connector 3.7.0, pyarrow, scipy, psycopg2-binary 2.9.12, pandas/numpy) ships 3.13 wheels — no dependency had to be dropped.

**Negative / watch**
- **3.14 is deferred** until GX supports it non-experimentally; revisit when GX drops the `GX_PYTHON_EXPERIMENTAL` gate.
- Contributors must rebuild their conda env (`conda env create -f environment.yml`, or `scripts/reset_dev_db.sh` is unaffected); CI's `setup-python` handles it automatically.

## Validation

A fresh Python 3.13.13 conda env built from the updated `requirements-dev.txt`: **`pip-audit` reports no known vulnerabilities**, and the **full backend suite passes (512 tests)** with the Snowflake adapter importing cleanly against connector 4.x.
