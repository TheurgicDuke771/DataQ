# DataQ backend image — serves both the FastAPI API and the Celery worker.
# Build context is the repo ROOT (not backend/) because the app imports as the
# top-level `backend.app.*` package:
#   docker build -f backend/Dockerfile .
#
# Multi-stage pip build on python:3.13-slim (ADR 0025). The production image uses
# pip, NOT conda: every dependency already came through pip (environment.yml only
# `-r`'d the requirements chain — conda installed no conda-channel packages), so
# the miniconda base added ~1.5-2GB and build friction for no benefit. Conda stays
# the LOCAL-DEV tool (environment.yml / setup.sh) per CLAUDE.md; only this image
# changed. RUNTIME deps only (requirements.txt) — tests/tooling run in the dev env
# (conda locally, pip in CI), never in this image.

# ── Builder: install deps into an isolated venv ──────────────────────────────
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential covers any sdist-only transitive dep; it lives ONLY in this
# stage and never reaches the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Copy just the requirements chain first so this layer caches on pin changes
# only, not on every code edit. requirements.txt is the runtime SINGLE SOURCE OF
# TRUTH (the dev/typecheck/tooling files are not installed into the image).
COPY backend/requirements*.txt ./backend/
RUN pip install -r backend/requirements.txt

# ── Runtime: slim base + the venv + app code ─────────────────────────────────
FROM python:3.13-slim AS runtime

# Link the GHCR package to this repo (shows under the repo's Packages and lets the
# repo's Actions GITHUB_TOKEN push to it — ADR 0023/0024). Without this the package
# is user-scoped + unlinked, so CI can't push and ACA-pull access isn't inherited.
LABEL org.opencontainers.image.source="https://github.com/TheurgicDuke771/DataQ"
LABEL org.opencontainers.image.description="DataQ backend (FastAPI API + Celery worker)"
LABEL org.opencontainers.image.licenses="MIT"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace

# The fully-built venv from the builder (no compilers / apt lists in this layer).
COPY --from=builder /opt/venv /opt/venv

# Application code + tooling config (pyproject is imported for runtime config).
COPY pyproject.toml ./
COPY backend ./backend

EXPOSE 8000

# Default to the API; the worker overrides `command` (compose / Container App),
# and the migrate job runs `alembic upgrade head`. psycopg2-binary (a wheel)
# keeps the image apt-free; the psycopg2-source swap remains a deferred nicety.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
