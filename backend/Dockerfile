# DataQ backend image — serves both the FastAPI API and the Celery worker.
# Build context is the repo ROOT (not backend/) because the app imports as the
# top-level `backend.app.*` package and environment.yml lives at the root:
#   docker build -f backend/Dockerfile .
#
# Conda-only per CLAUDE.md (no venv/poetry). The dataq env is created from the
# pinned environment.yml and its bin dir is put first on PATH so `uvicorn`,
# `celery`, `alembic`, and `python` resolve to it without a `conda run` wrapper
# (which would interfere with signal forwarding to uvicorn/celery).
FROM continuumio/miniconda3:24.11.1-0

# Link the GHCR package to this repo (shows under the repo's Packages and lets the
# repo's Actions GITHUB_TOKEN push to it — ADR 0023/0024). Without this the package
# is user-scoped + unlinked, so CI can't push and ACA-pull access isn't inherited.
LABEL org.opencontainers.image.source="https://github.com/TheurgicDuke771/DataQ"
LABEL org.opencontainers.image.description="DataQ backend (FastAPI API + Celery worker)"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /workspace

# Create the conda env first and cache the layer. environment.yml's pip section
# does `-r backend/requirements-dev.txt` (which -r's requirements.txt /
# -typecheck / -tooling), so those must exist before `conda env create`. Copy
# just the requirements files (not all of backend/) so the env-create layer cache
# busts only on a pin change, not on every code edit.
COPY environment.yml ./
COPY backend/requirements*.txt ./backend/
RUN conda env create -f environment.yml && conda clean -afy

ENV PATH=/opt/conda/envs/dataq/bin:$PATH \
    PYTHONUNBUFFERED=1

# Application code + tooling config (pyproject is needed to run pytest/mypy in-container).
COPY pyproject.toml ./
COPY backend ./backend

EXPOSE 8000

# Default to the API; the worker service overrides `command` in compose.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
