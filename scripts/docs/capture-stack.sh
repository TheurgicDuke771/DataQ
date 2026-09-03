#!/usr/bin/env bash
# Scratch stack for the docs screenshot/video lane (frontend/e2e-docs/).
# Mirrors CI's frontend-e2e job — dev-bypass auth, seeded demo data — on its
# own database, OpenBao KV mount and ports, so the developer's compose stack
# (and its orphan-secret sweep) never sees it. Reuses the compose Postgres
# (:5432), Redis (:6379, db 3) and OpenBao (:8200).
#
#   scripts/docs/capture-stack.sh start    # migrate + seed + api :8001 + vite :3001
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
  curl -sf "http://127.0.0.1:${API_PORT}/api/v1/me" >/dev/null || { echo "api did not come up"; tail -20 "$RUN/api.log"; exit 1; }
  # The seed carries no llm_settings row; the LLM captures need a saved, enabled
  # OpenAI-compatible config so a fresh stack regenerates the same images. The
  # key is a placeholder (Ollama ignores it); Test only shows OK when a server
  # answers at DOCS_LLM_BASE_URL.
  curl -sf -o /dev/null -X PUT -H 'Content-Type: application/json' \
    "http://127.0.0.1:${API_PORT}/api/v1/admin/llm" \
    -d "{\"provider\":\"openai_compatible\",\"model\":\"${DOCS_LLM_MODEL:-qwen2.5:14b}\",\"base_url\":\"${DOCS_LLM_BASE_URL:-http://127.0.0.1:11434/v1}\",\"api_key\":\"docs-capture-placeholder\",\"structured_output\":\"prompt_json\",\"enabled\":true}" \
    || { echo "could not seed the LLM provider config"; exit 1; }
  (cd "$ROOT/frontend" && VITE_API_PROXY_TARGET="http://127.0.0.1:${API_PORT}" VITE_AUTH_DEV_BYPASS=true \
      nohup pnpm dev --host 127.0.0.1 --port "$WEB_PORT" --strictPort >"$RUN/web.log" 2>&1 </dev/null & echo $! >"$RUN/web.pid")
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null && break; sleep 1
  done
  curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null || { echo "vite did not come up"; tail -20 "$RUN/web.log"; exit 1; }
  echo "docs capture stack up: web http://127.0.0.1:${WEB_PORT} api http://127.0.0.1:${API_PORT} (logs in $RUN)"
}

capture() {
  (cd "$ROOT/frontend" && E2E_DOCS=1 E2E_DOCS_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      pnpm exec playwright test --project docs-capture "$@")
}

stop() {
  for p in api web; do
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
  echo "stopped"
}

destroy() {
  _env
  stop
  _psql "drop database if exists ${DB_NAME}" >/dev/null && echo "dropped ${DB_NAME}"
  _bao DELETE "sys/mounts/${KV_MOUNT}" >/dev/null 2>&1 && echo "removed KV mount ${KV_MOUNT}" || true
  rm -f "$RUN/.role-tokens.json"
}

case "${1:-}" in
  start) start ;;
  capture) shift; capture "$@" ;;
  stop) stop ;;
  destroy) destroy ;;
  *) echo "usage: $0 start|capture|stop|destroy"; exit 2 ;;
esac
