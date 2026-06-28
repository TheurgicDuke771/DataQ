# ADR 0025 — Production image: pip on `python:3.13-slim` (multi-stage); conda retained for local dev

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0017](0017-python-313-runtime-upgrade.md) (Python 3.13 runtime — same pinned
  interpreter, different packager), [0023](0023-container-image-registry-ghcr.md) (GHCR pulls cross the
  internet → image size = cold-start/scale-out latency), [0024](0024-app-deployment-infrastructure.md)
  (ACA deploy this image runs on). Amends — does **not** fully revoke — the Week-1 "conda only" tooling
  lock (CLAUDE.md §6/§11): conda stays for local dev; only the container image changes.

## Context

The backend image was built on `continuumio/miniconda3` and created the `dataq` conda env at build
time. But `environment.yml` installs **nothing from conda channels** except the Python interpreter +
pip — its entire `dependencies:` is `pip: [-r backend/requirements-dev.txt]`. So conda's one real
advantage (prebuilt binary packages from conda-forge) was **unused**: all ~30 runtime deps already come
from pip wheels.

Meanwhile the Week-7 cloud move made image size load-bearing: the conda image was **~2.84 GB**, GHCR
pulls cross the internet (ADR 0023), and the amd64 image must be built under emulation locally. We were
paying conda's full cost (base image + env + build friction) for none of its benefit.

## Decision

**Build the production image with pip on a multi-stage `python:3.13-slim`. Keep conda as the local-dev
tool unchanged.**

- **Multi-stage** ([backend/Dockerfile](../../backend/Dockerfile)): a `builder` stage (`python:3.13-slim`
  + `build-essential`, discarded) installs `requirements.txt` into a `/opt/venv`; the `runtime` stage is
  `python:3.13-slim` + that venv + the app code. No compilers / apt lists in the final layer.
- **Runtime deps only** — the image installs `requirements.txt`, not the dev/typecheck/tooling chain.
  Tests + linters run in the dev env (conda locally, `pip install` in CI), **never in the image**. CI
  already uses `actions/setup-python` + pip directly, so it is unaffected.
- **Same pinned deps + interpreter** — `requirements.txt` stays the single source of truth and Python
  stays 3.13 (ADR 0017). Only the packager/base image changed.
- **Local dev unchanged** — `environment.yml` / `setup.sh` / the conda workflow stay. CLAUDE.md's "conda
  only" now means *local dev*; the production image is pip-on-slim.
- `psycopg2-binary` (a wheel) is kept so the image stays apt-free; the psycopg2-source swap noted in
  `requirements.txt` remains a deferred nicety.

## Consequences

**Positive**
- **Image ~2.84 GB → ~0.8–1.2 GB.** Faster ACA cold-start + scale-out and smaller cross-internet GHCR
  pulls (the ADR 0023 concern), and a faster local amd64 emulated build.
- Smaller attack surface (no conda toolchain / build tools / dev+test deps in the runtime image).
- Standard, portable container idiom (venv + slim) — friendlier for the BYOL/marketplace image (ADR
  0013) than a conda base.

**Negative / watch**
- **Dev/prod packager split** (conda locally, pip in the image). Mitigated because deps + versions +
  interpreter are identical (one requirements source, same 3.13); only the install mechanism differs.
- In-container `pytest`/`mypy` no longer work (runtime-only image) — intentional; use the dev env.
- A future conda-only dependency would reopen this; none exists today (all deps ship 3.13 wheels).

## Alternatives considered

- **Keep conda in the image.** Rejected: ~2× the image size + slower builds for zero functional gain
  (no conda-channel packages in use).
- **Full switch to venv+pip everywhere** (retire conda, drop the §6 rule). Deferred: larger blast radius
  (setup.sh / dev docs / contributor habits) for little extra benefit beyond this image. Can be a later
  ADR if the dev/prod split proves annoying.
- **Distroless / Alpine base.** Rejected for now: distroless complicates the venv + shell-form migrate
  command; Alpine's musl breaks many manylinux wheels (would force source builds). `slim` (glibc) keeps
  wheels working with most of the size win.
