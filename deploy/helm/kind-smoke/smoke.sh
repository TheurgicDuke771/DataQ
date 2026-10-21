#!/usr/bin/env bash
# Installs the chart on a throwaway kind cluster and checks it end to end: the migrate hook,
# every component up on a read-only root filesystem, an OTP sign-in through the frontend, the
# api unreachable except from the frontend, an upgrade, and a failed migration blocking a roll.
#
#   deploy/helm/kind-smoke/smoke.sh          # KEEP=1 leaves the cluster up afterwards
#
# Needs docker, kind, kubectl, helm, curl and python3. Pulls the published images.
set -euo pipefail

cluster=${KIND_CLUSTER:-dataq-smoke}
ns=dataq
here=$(cd "$(dirname "$0")" && pwd)
chart="$here/../dataq"
tag=${DATAQ_TAG:-$(awk '/^appVersion:/ {gsub(/"/, "", $2); print $2}' "$chart/Chart.yaml")}
fe_port=18080
mail_port=18025
pids=()

cleanup() {
  for pid in ${pids[@]+"${pids[@]}"}; do kill "$pid" 2>/dev/null || true; done
  if [ "${KEEP:-}" != "1" ]; then kind delete cluster --name "$cluster" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
k() { kubectl -n "$ns" "$@"; }

step "kind cluster $cluster"
kind get clusters 2>/dev/null | grep -qx "$cluster" || kind create cluster --name "$cluster" --wait 120s
kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
# Pulled once on the host and imported into the node, rather than pulled again inside it.
# (`kind load` asks for every platform of a multi-arch image and fails when only one is local.)
for image in dataq-backend dataq-frontend; do
  ref="ghcr.io/theurgicduke771/$image:$tag"
  docker pull -q "$ref" >/dev/null
  docker save "$ref" | docker exec -i "$cluster-control-plane" ctr -n k8s.io images import - >/dev/null
done

step "throwaway dependencies (credentials generated now, never written to disk)"
pg_password=$(openssl rand -hex 16)
bao_token=$(openssl rand -hex 16)
k create secret generic smoke-deps \
  --from-literal=postgres-password="$pg_password" --from-literal=openbao-token="$bao_token" \
  --dry-run=client -o yaml | k apply -f -
k create secret generic dataq-env \
  --from-literal=DATABASE_URL="postgresql+psycopg2://dataq:${pg_password}@postgres:5432/dataq" \
  --from-literal=REDIS_URL="redis://redis:6379/0" \
  --from-literal=OPENBAO_TOKEN="$bao_token" \
  --dry-run=client -o yaml | k apply -f -
k apply -f "$here/deps.yaml"
for d in postgres redis openbao mailpit demo-warehouse; do k rollout status "deploy/$d" --timeout=180s; done
# Mailpit accepts any SMTP password; the api still looks one up by name, so store a random one.
k exec deploy/openbao -- sh -c \
  'BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$BAO_DEV_ROOT_TOKEN_ID bao kv put secret/dataq-smtp value=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d " \n") >/dev/null'

step "helm install (tag $tag)"
start=$(date +%s)
helm upgrade --install dataq "$chart" -n "$ns" -f "$here/values.yaml" \
  --set image.backend.tag="$tag" --set image.frontend.tag="$tag" --wait --timeout 10m
echo "installed in $(( $(date +%s) - start ))s"
k get pods -o wide

step "the migrate hook ran and was cleaned up"
k exec deploy/postgres -- psql -U dataq -d dataq -tAc "select version_num from alembic_version" \
  | grep -Eq '^[0-9a-f]{12}$' || fail "alembic_version is empty"
k get job dataq-migrate >/dev/null 2>&1 && fail "the succeeded migrate Job was not deleted"

step "every pod is non-root on a read-only root filesystem"
for c in api worker beat frontend; do
  pod=$(k get pod -l app.kubernetes.io/component=$c -o jsonpath='{.items[0].metadata.name}')
  uid=$(k exec "$pod" -c "$c" -- id -u)
  [ "$uid" != "0" ] || fail "$c runs as root"
  if k exec "$pod" -c "$c" -- sh -c 'touch /probe-ro 2>/dev/null'; then fail "$c root fs is writable"; fi
  echo "$c: uid $uid, read-only root"
done
k logs deploy/dataq-worker | grep -q "ready" || fail "worker not ready"
k logs deploy/dataq-beat | grep -q "beat: Starting" || fail "beat did not start"

step "through the frontend"
k port-forward svc/dataq-frontend "$fe_port:80" >/dev/null 2>&1 & pids+=($!)
k port-forward svc/mailpit "$mail_port:8025" >/dev/null 2>&1 & pids+=($!)
sleep 3
base="http://127.0.0.1:$fe_port"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$base/healthz")" = 200 ] || fail "/healthz"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$base/api/v1/me")" = 401 ] || fail "/api/v1/me unauthenticated"
curl -s "$base/config.js" | grep -q '"mode":"otp"' || fail "frontend auth mode"
echo "healthz 200, /api/v1/me 401, config.js mode otp"

step "OTP sign-in end to end"
jar=$(mktemp)
curl -s -o /dev/null -X POST "$base/api/v1/auth/otp/request" -H 'content-type: application/json' \
  -d '{"email":"admin@example.com"}'
code=""
for _ in $(seq 1 20); do
  code=$(curl -s "http://127.0.0.1:$mail_port/api/v1/messages" | python3 -c '
import json, re, sys
msgs = json.load(sys.stdin).get("messages") or []
m = re.search(r"\b(\d{6})\b", msgs[0]["Snippet"]) if msgs else None
print(m.group(1) if m else "")')
  [ -n "$code" ] && break
  sleep 1
done
[ -n "$code" ] || fail "no sign-in code arrived"
printf '{"email":"admin@example.com","code":"%s"}' "$code" >"$jar.body"
[ "$(curl -s -o /dev/null -w '%{http_code}' -c "$jar" -X POST "$base/api/v1/auth/otp/verify" \
  -H 'content-type: application/json' --data @"$jar.body")" = 200 ] || fail "otp verify"
curl -s -b "$jar" "$base/api/v1/me" | grep -q '"email":"admin@example.com"' || fail "/me after sign-in"
rm -f "$jar" "$jar.body"
echo "signed in as admin@example.com"

step "a suite run through the worker and the vault"
k exec deploy/dataq-api -- env DEMO_WAREHOUSE_HOST=demo-warehouse python -m backend.scripts.seed_demo >/dev/null
runs=""
for _ in $(seq 1 60); do
  runs=$(k exec deploy/postgres -- psql -U dataq -d dataq -tAc \
    "select string_agg(status, ',') from runs r join suites s on s.id = r.suite_id join connections c on c.id = s.connection_id where c.name like 'Demo warehouse%'")
  case "$runs" in *queued*|*running*|"") sleep 3 ;; *) break ;; esac
done
case "$runs" in *succeeded*) ;; *) fail "demo suite runs ended as: ${runs:-none}" ;; esac
case "$runs" in *failed*) fail "a demo suite run failed: $runs" ;; esac
echo "demo suite runs: $runs"

step "NetworkPolicy: the api answers the frontend only"
fe_pod=$(k get pod -l app.kubernetes.io/component=frontend -o jsonpath='{.items[0].metadata.name}')
k exec "$fe_pod" -c frontend -- wget -q -O /dev/null -T 5 http://dataq-api:8000/healthz \
  || fail "frontend cannot reach the api"
if k run np-probe --rm -i --restart=Never --image=busybox:1.37 -- \
  wget -q -O /dev/null -T 5 http://dataq-api:8000/healthz >/dev/null 2>&1; then
  fail "a non-frontend pod reached the api"
fi
echo "frontend -> api allowed, other pod -> api refused"

step "helm upgrade runs the hook again and rolls"
before=$(k get deploy dataq-api -o jsonpath='{.metadata.generation}')
helm upgrade dataq "$chart" -n "$ns" -f "$here/values.yaml" --reuse-values \
  --set config.logLevel=DEBUG --wait --timeout 10m
after=$(k get deploy dataq-api -o jsonpath='{.metadata.generation}')
[ "$after" -gt "$before" ] || fail "the api did not roll"
echo "api generation $before -> $after"

step "a failed migration blocks the roll"
k create secret generic dataq-broken-db \
  --from-literal=DATABASE_URL="postgresql+psycopg2://dataq:wrong@postgres:5432/dataq" \
  --dry-run=client -o yaml | k apply -f -
before=$(k get deploy dataq-api -o jsonpath='{.metadata.generation}')
if helm upgrade dataq "$chart" -n "$ns" -f "$here/values.yaml" --reuse-values \
  --set 'extraEnvFromSecrets={dataq-broken-db}' --set migrate.activeDeadlineSeconds=120 \
  --wait --timeout 4m >/dev/null 2>&1; then
  fail "the upgrade succeeded with a broken migration"
fi
after=$(k get deploy dataq-api -o jsonpath='{.metadata.generation}')
[ "$after" = "$before" ] || fail "the api rolled although the migration failed"
k get job dataq-migrate -o jsonpath='{.status.failed}' | grep -q 1 || fail "the failed Job was not kept"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$base/healthz")" = 200 ] || fail "/healthz after the failed upgrade"
echo "upgrade refused, api still on generation $after and serving"

step "PASS"
