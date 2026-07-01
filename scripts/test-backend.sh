#!/usr/bin/env bash
# Run the backend test suite locally against the docker-compose Postgres + Redis,
# so the ~450 DB-backed tests RUN instead of skipping (they need real Postgres for
# gen_random_uuid()/jsonb — see backend/tests/conftest.py). This mirrors what CI
# does. Without these services `pytest` still passes, just skipping the DB tests.
#
#   scripts/test-backend.sh                      # full suite
#   scripts/test-backend.sh backend/tests/api    # a subset
#   scripts/test-backend.sh -k notifications -x  # extra pytest args pass through
#
# Safe: tests run against a dedicated `dataq_test` database (create_all/drop_all +
# per-test rollback), never the dev `${POSTGRES_DB}` with your seed data.
set -euo pipefail
cd "$(dirname "$0")/.."

# Local infra creds (POSTGRES_USER/PASSWORD/DB) live in the gitignored .env that
# scripts/setup.sh generates.
if [ ! -f .env ]; then
  echo "error: .env not found — run scripts/setup.sh first (it generates the local creds)." >&2
  exit 1
fi
set -a; . ./.env; set +a
: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set in .env}"

echo "==> ensuring postgres + redis are up (docker compose)…"
docker compose up -d postgres redis >/dev/null
until docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do
  echo "   waiting for postgres…"; sleep 1
done

# Dedicated test DB, separate from the dev DB. Idempotent.
if ! docker compose exec -T postgres psql -U "$POSTGRES_USER" -tAc \
      "SELECT 1 FROM pg_database WHERE datname='dataq_test'" | grep -q 1; then
  echo "==> creating dataq_test database…"
  docker compose exec -T postgres createdb -U "$POSTGRES_USER" dataq_test
fi

TEST_URL="postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/dataq_test"
echo "==> running pytest against dataq_test + local redis…"
exec conda run -n dataq --no-capture-output env \
  TEST_DATABASE_URL="$TEST_URL" \
  DATABASE_URL="$TEST_URL" \
  REDIS_URL="redis://localhost:6379/0" \
  python -m pytest backend/tests "$@"
