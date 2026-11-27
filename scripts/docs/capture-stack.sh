#!/usr/bin/env bash
# Scratch stack for the docs screenshot/video lane (frontend/e2e-docs/).
# Mirrors CI's frontend-e2e job — dev-bypass auth, seeded demo data — on its
# own database, OpenBao KV mount and ports, so the developer's compose stack
# (and its orphan-secret sweep) never sees it. Reuses the compose Postgres
# (:5432), Redis (:6379, db 3) and OpenBao (:8200).
#
#   scripts/docs/capture-stack.sh start    # migrate + seed + warehouse :55433 + api :8001 + worker + vite :3001
#   scripts/docs/capture-stack.sh capture  # run the e2e-docs Playwright project
#   scripts/docs/capture-stack.sh stop     # stop the processes (data kept)
#   scripts/docs/capture-stack.sh destroy  # stop + drop the database + the KV mount
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="${TMPDIR:-/tmp}/dataq-docs-capture"
mkdir -p "$RUN"
API_PORT="${DOCS_API_PORT:-8001}"
WEB_PORT="${DOCS_WEB_PORT:-3001}"
DB_NAME="${DOCS_DB_NAME:-dataq_docs}"
KV_MOUNT="${DOCS_KV_MOUNT:-docs-capture}"
WH_PORT="${DOCS_WAREHOUSE_PORT:-55433}"
WH_CONTAINER="${DOCS_WAREHOUSE_CONTAINER:-dataq-docs-warehouse}"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml")

_env() {
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  export DATABASE_URL="postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${DB_NAME}"
  export REDIS_URL="redis://localhost:6379/3"
  export SECRET_STORE=openbao
  export OPENBAO_ADDR="http://localhost:8200"
  export OPENBAO_TOKEN
  export OPENBAO_MOUNT="$KV_MOUNT"
  export AUTH_DEV_BYPASS=true
  # Explicitly NOT OTP: a developer's .env.app may configure it, and OTP wins
  # over dev-bypass at import time (core/auth.py).
  export AUTH_OTP_ALLOWED_EMAILS= AUTH_OTP_ALLOWED_DOMAINS= AUTH_EMAIL_SMTP_HOST= AUTH_EMAIL_FROM=
  export DATAQ_SIGNIN_EMAIL=
  export ENVIRONMENT=dev
  export WORKSPACE_ADMIN_EMAILS=dev-bypass@dataq.local
  export RATE_LIMIT_ENABLED=false
  export DATAQ_ROLE_TOKENS_PATH="$RUN/.role-tokens.json"
}

# A failure after the API started stops what is running, so no half-started stack is left behind.
_abort() { echo "$1"; [ -n "${2:-}" ] && tail -20 "$2"; stop >/dev/null; exit 1; }

_alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

_psql() { "${COMPOSE[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d postgres -tAc "$1"; }

_bao() { curl -sf -X "$1" -H "X-Vault-Token: $OPENBAO_TOKEN" "http://localhost:8200/v1/$2" "${@:3}"; }

start() {
  _env
  if _alive api || _alive web; then
    echo "already running (pids in $RUN) — run 'stop' first"; exit 1
  fi
  _psql "select 1 from pg_database where datname='${DB_NAME}'" | grep -q 1 \
    || _psql "create database ${DB_NAME}" >/dev/null
  _bao GET "sys/mounts/${KV_MOUNT}" >/dev/null 2>&1 \
    || _bao POST "sys/mounts/${KV_MOUNT}" -H 'Content-Type: application/json' \
         -d '{"type":"kv","options":{"version":"2"}}' >/dev/null
  (cd "$ROOT/backend" && conda run -n dataq --no-capture-output alembic upgrade head >"$RUN/alembic.log" 2>&1)
  (cd "$ROOT" && conda run -n dataq --no-capture-output python -m backend.scripts.seed_dev >"$RUN/seed.log" 2>&1)
  (cd "$ROOT" && nohup conda run -n dataq --no-capture-output \
      uvicorn backend.app.main:app --host 127.0.0.1 --port "$API_PORT" >"$RUN/api.log" 2>&1 </dev/null & echo $! >"$RUN/api.pid")
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${API_PORT}/api/v1/me" >/dev/null && break; sleep 1
  done
  curl -sf "http://127.0.0.1:${API_PORT}/api/v1/me" >/dev/null || _abort "api did not come up" "$RUN/api.log"
  # A worker on the stack's own queue (Redis db 3): the LLM captures (SQL generation, check
  # suggestions) dispatch to it and poll until it finishes. The compose worker listens elsewhere.
  (cd "$ROOT" && nohup conda run -n dataq --no-capture-output \
      celery -A backend.app.worker.celery_app worker -Q celery,llm --concurrency 2 --loglevel=INFO \
      >"$RUN/worker.log" 2>&1 </dev/null & echo $! >"$RUN/worker.pid")
  # The seed carries no llm_settings row; the LLM captures need a saved, enabled
  # OpenAI-compatible config so a fresh stack regenerates the same images. The
  # key is a placeholder (Ollama ignores it). The API tests the provider on save, so
  # with nothing answering at DOCS_LLM_BASE_URL the save is refused: the stack still
  # starts, and only the LLM captures are unavailable.
  curl -sf -o /dev/null -X PUT -H 'Content-Type: application/json' \
    "http://127.0.0.1:${API_PORT}/api/v1/admin/llm" \
    -d "{\"provider\":\"openai_compatible\",\"model\":\"${DOCS_LLM_MODEL:-qwen2.5:14b}\",\"base_url\":\"${DOCS_LLM_BASE_URL:-http://127.0.0.1:11434/v1}\",\"api_key\":\"docs-capture-placeholder\",\"structured_output\":\"prompt_json\",\"enabled\":true}" \
    || echo "warning: LLM provider config not saved (no server answering at ${DOCS_LLM_BASE_URL:-http://127.0.0.1:11434/v1}?) — continuing without the LLM captures"
  # The SQL-generation and suggest-checks captures introspect a live table, so the stack runs
  # its own small PostgreSQL warehouse (the compose `demo` profile's image and shop dataset,
  # on loopback only) and connects DataQ to it the way `seed_demo` does for an evaluator.
  docker rm -f "$WH_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$WH_CONTAINER" -p "127.0.0.1:${WH_PORT}:5432" \
      -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=demo -e POSTGRES_DB=shop \
      postgres:16-alpine >/dev/null || _abort "could not start the docs warehouse"
  for _ in $(seq 1 30); do
    docker exec "$WH_CONTAINER" pg_isready -U demo -d shop >/dev/null 2>&1 && break; sleep 1
  done
  docker exec "$WH_CONTAINER" pg_isready -U demo -d shop >/dev/null 2>&1 \
    || _abort "the docs warehouse did not come up"
  (cd "$ROOT" && DEMO_WAREHOUSE_HOST=127.0.0.1 DEMO_WAREHOUSE_PORT="$WH_PORT" \
      conda run -n dataq --no-capture-output python -m backend.scripts.seed_demo >"$RUN/seed_demo.log" 2>&1) \
    || _abort "could not seed the docs warehouse" "$RUN/seed_demo.log"
  (cd "$ROOT/frontend" && VITE_API_PROXY_TARGET="http://127.0.0.1:${API_PORT}" VITE_AUTH_DEV_BYPASS=true \
      nohup pnpm dev --host 127.0.0.1 --port "$WEB_PORT" --strictPort >"$RUN/web.log" 2>&1 </dev/null & echo $! >"$RUN/web.pid")
  # Vite must be the process answering: with the port already taken it exits, and
  # whatever else listens there would pass a bare curl of `/`.
  for _ in $(seq 1 40); do
    _alive web || break
    curl -sf "http://127.0.0.1:${WEB_PORT}/@vite/client" >/dev/null && break; sleep 1
  done
  { _alive web && curl -sf "http://127.0.0.1:${WEB_PORT}/@vite/client" >/dev/null; } \
    || _abort "vite did not come up" "$RUN/web.log"
  echo "docs capture stack up: web http://127.0.0.1:${WEB_PORT} api http://127.0.0.1:${API_PORT} (logs in $RUN)"
}

capture() {
  (cd "$ROOT/frontend" && E2E_DOCS=1 E2E_DOCS_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      pnpm exec playwright test --project docs-capture "$@")
}

stop() {
  for p in api web worker; do
    if [ -f "$RUN/$p.pid" ]; then
      pid="$(cat "$RUN/$p.pid")"
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      rm -f "$RUN/$p.pid"
    fi
  done
  # Belt and braces for orphans: match the real argv shapes (uvicorn's own
  # process; Vite runs as node …/vite.js --host … --port N).
  pkill -f "uvicorn backend.app.main:app --host 127.0.0.1 --port ${API_PORT}" 2>/dev/null || true
  pkill -f "vite.js --host 127.0.0.1 --port ${WEB_PORT}" 2>/dev/null || true
  pkill -f "celery -A backend.app.worker.celery_app worker -Q celery,llm --concurrency 2" 2>/dev/null || true
  echo "stopped"
}

destroy() {
  _env
  stop
  _psql "drop database if exists ${DB_NAME}" >/dev/null && echo "dropped ${DB_NAME}"
  _bao DELETE "sys/mounts/${KV_MOUNT}" >/dev/null 2>&1 && echo "removed KV mount ${KV_MOUNT}" || true
  docker rm -f "$WH_CONTAINER" >/dev/null 2>&1 && echo "removed ${WH_CONTAINER}" || true
  rm -f "$RUN/.role-tokens.json"
}

case "${1:-}" in
  start) start ;;
  capture) shift; capture "$@" ;;
  stop) stop ;;
  destroy) destroy ;;
  *) echo "usage: $0 start|capture|stop|destroy"; exit 2 ;;
esac
