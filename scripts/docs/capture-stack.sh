#!/usr/bin/env bash
# Scratch stack for the docs screenshot/video lane (frontend/e2e-docs/).
# Mirrors CI's frontend-e2e job — dev-bypass auth, seeded demo data — on its
# own database and ports so the developer's compose stack is never touched.
# Reuses the compose Postgres (:5432), Redis (:6379, db 3) and OpenBao (:8200).
#
#   scripts/docs/capture-stack.sh start   # migrate + seed + api :8001 + vite :3001
#   scripts/docs/capture-stack.sh capture # run the e2e-docs Playwright project
#   scripts/docs/capture-stack.sh stop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="${TMPDIR:-/tmp}/dataq-docs-capture"
mkdir -p "$RUN"
API_PORT="${DOCS_API_PORT:-8001}"
WEB_PORT="${DOCS_WEB_PORT:-3001}"
DB_NAME="${DOCS_DB_NAME:-dataq_docs}"

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
  export AUTH_DEV_BYPASS=true
  export ENVIRONMENT=dev
  export WORKSPACE_ADMIN_EMAILS=dev-bypass@dataq.local
  export RATE_LIMIT_ENABLED=false
  export DATAQ_SIGNIN_EMAIL=
}

start() {
  _env
  docker compose -f "$ROOT/docker-compose.yml" exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres -tAc "select 1 from pg_database where datname='${DB_NAME}'" | grep -q 1 \
    || docker compose -f "$ROOT/docker-compose.yml" exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "create database ${DB_NAME}" >/dev/null
  (cd "$ROOT/backend" && conda run -n dataq --no-capture-output alembic upgrade head >"$RUN/alembic.log" 2>&1)
  (cd "$ROOT" && conda run -n dataq --no-capture-output python -m backend.scripts.seed_dev >"$RUN/seed.log" 2>&1)
  (cd "$ROOT" && nohup conda run -n dataq --no-capture-output \
      uvicorn backend.app.main:app --host 127.0.0.1 --port "$API_PORT" >"$RUN/api.log" 2>&1 </dev/null & echo $! >"$RUN/api.pid")
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${API_PORT}/api/v1/me" >/dev/null && break; sleep 1
  done
  curl -sf "http://127.0.0.1:${API_PORT}/api/v1/me" >/dev/null || { echo "api did not come up"; tail -20 "$RUN/api.log"; exit 1; }
  (cd "$ROOT/frontend" && VITE_API_PROXY_TARGET="http://127.0.0.1:${API_PORT}" VITE_AUTH_DEV_BYPASS=true \
      nohup pnpm dev --host 127.0.0.1 --port "$WEB_PORT" >"$RUN/web.log" 2>&1 </dev/null & echo $! >"$RUN/web.pid")
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null && break; sleep 1
  done
  echo "docs capture stack up: web http://127.0.0.1:${WEB_PORT} api http://127.0.0.1:${API_PORT} (logs in $RUN)"
}

capture() {
  (cd "$ROOT/frontend" && E2E_DOCS=1 E2E_DOCS_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      pnpm exec playwright test --project docs-capture "$@")
}

stop() {
  for p in api web; do
    [ -f "$RUN/$p.pid" ] && { pkill -P "$(cat "$RUN/$p.pid")" 2>/dev/null || true; kill "$(cat "$RUN/$p.pid")" 2>/dev/null || true; rm -f "$RUN/$p.pid"; }
  done
  pkill -f "uvicorn backend.app.main:app --host 127.0.0.1 --port ${API_PORT}" 2>/dev/null || true
  pkill -f "vite --host 127.0.0.1 --port ${WEB_PORT}" 2>/dev/null || true
  echo "stopped"
}

case "${1:-}" in
  start) start ;;
  capture) shift; capture "$@" ;;
  stop) stop ;;
  *) echo "usage: $0 start|capture|stop"; exit 2 ;;
esac
