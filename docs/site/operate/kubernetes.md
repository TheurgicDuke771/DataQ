# Install on Kubernetes (Helm)

The Helm chart at
[`deploy/helm/dataq/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/helm/dataq)
installs DataQ into any Kubernetes cluster (1.25 or later): AKS, EKS, GKE, OpenShift or your
own. It runs the same published images as the cloud reference deployments and the
prebuilt-image Docker stack.

## What it installs

| Component | Kind | Notes |
|---|---|---|
| api | Deployment + ClusterIP Service | Never exposed outside the cluster. |
| worker | Deployment | Consumes both task queues. Scale it with `worker.replicas`. |
| beat | Deployment | The scheduler. Always exactly one pod, replaced rather than rolled, so periodic tasks never fire twice. |
| frontend | Deployment + Service | The only way in. Serves the app and proxies `/api`, `/mcp` and `/healthz` to the api. |
| migrate | Job (pre-install / pre-upgrade hook) | Runs `alembic upgrade head` before anything rolls. |
| Ingress | optional, off by default | Routes to the frontend only. |
| PodDisruptionBudgets | when a component runs more than one replica | api, worker, frontend. |
| NetworkPolicy | optional, off by default | The api accepts traffic from the frontend pods only; worker and beat accept none. |

It does **not** install PostgreSQL, Redis or a vault. Use managed services or ones you already
run; the chart takes their addresses from a Secret you create.

Every pod runs as a non-root user on a read-only root filesystem, with all capabilities
dropped and no Kubernetes API token mounted.

## Before you install

1. **PostgreSQL 16** with an empty database and a role that owns it, and **Redis 7**.
2. **A secret store** for connection credentials: OpenBao or Vault (KV v2), Azure Key Vault
   or AWS Secrets Manager.
3. **A sign-in method**: an OIDC provider, or an SMTP relay for emailed one-time codes.
4. **A Secret** in the release namespace holding the credentials. The chart never takes a
   credential as a value; it reads this Secret by name.

```bash
kubectl create namespace dataq
kubectl -n dataq create secret generic dataq-env \
  --from-literal=DATABASE_URL='postgresql+psycopg2://dataq:<password>@<host>:5432/dataq?sslmode=verify-full' \
  --from-literal=REDIS_URL='rediss://:<password>@<host>:6380/0?ssl_cert_reqs=required' \
  --from-literal=OPENBAO_TOKEN='<token>'
```

Every key in this Secret becomes an environment variable on the api, worker, beat and the
migration job. Use `OPENBAO_ROLE_ID` and `OPENBAO_SECRET_ID` instead of a token for AppRole
sign-in. With a cloud secret store there is no vault credential: give the pods an identity
through `serviceAccount.annotations` (EKS IRSA or Azure workload identity) instead. Further
credentials, such as a tamper-anchor webhook secret, go in the same Secret, or in another one
listed under `extraEnvFromSecrets`.

If your database, Redis or vault certificates come from a private CA, put the CA certificate
in a ConfigMap and name it in `trustedCA.existingConfigMap`. It is mounted at
`/etc/dataq/ca`, so `sslrootcert=/etc/dataq/ca/ca.pem` in `DATABASE_URL` and
`secretStore.openbao.caBundle: /etc/dataq/ca/ca.pem` find it.

## Install

A values file for OIDC sign-in, OpenBao and an ingress:

```yaml
existingSecret: dataq-env
auth:
  mode: oidc
  workspaceAdminEmails: you@example.com
  oidc:
    issuer: https://idp.example.com/realms/dataq
    audience: dataq-api
    clientId: dataq-spa
    allowedDomains: example.com
secretStore:
  backend: openbao
  openbao:
    addr: https://openbao.vault.svc:8200
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: dataq.example.com
  tls:
    - secretName: dataq-example-tls
      hosts: [dataq.example.com]
frontend:
  cspConnectSrc: https://idp.example.com
```

```bash
git clone --depth 1 --branch vX.Y.Z https://github.com/TheurgicDuke771/DataQ.git
helm install dataq DataQ/deploy/helm/dataq -n dataq -f my-values.yaml --wait --timeout 10m
```

The chart's `appVersion` is the release its images default to. Pin `image.backend.tag` and
`image.frontend.tag` (or a `digest`) if you install from a different checkout; the chart
refuses `latest`.

Then check it: `/healthz` through the frontend returns `200`, you can sign in, and a suite run
completes.

## Values that matter

| Value | What it does |
|---|---|
| `existingSecret` | **Required.** The Secret above. |
| `auth.mode` | `otp` (emailed codes; needs `auth.otp.*` and an allowlist), `oidc` (needs `issuer`, `audience`, `clientId`) or `bypass`. The chart sets both the api and the frontend from this one value. |
| `auth.allowDevBypass` | `bypass` signs everyone in as an admin with no credential. The chart refuses it unless this is `true` and `config.environment` is `dev`. |
| `secretStore.backend` | `openbao`, `azure_key_vault`, `aws_secrets_manager` or `env`. |
| `config.publicBaseUrl` | The browser-facing origin. Taken from the first ingress host when empty. |
| `config.rateLimitXffTrustedHops` | Proxies in front of the api that append `X-Forwarded-For`. The default works out 2 with the ingress on (controller and frontend) and 1 without. Count again if a load balancer or CDN adds its own. |
| `config.telemetry.sink` | Usage telemetry: `off` (default), `otlp` (through `config.otlpEndpoint`) or `file`. Nothing is recorded until a workspace admin also opts in. `file` needs `config.telemetry.existingClaim`, a PersistentVolumeClaim mounted at `/var/lib/dataq`, because the root filesystem is read-only. |
| `config.extraEnv` | Any other setting from the [environment reference](deployment.md), by name — for example the `TAMPER_ANCHOR_*` audit-anchor settings. A name that looks like a credential is refused: the anchor's webhook secret goes in `existingSecret`, while `TAMPER_ANCHOR_OBJECT_LOCK_ACCESS_KEY_ID` and the `*_SECRET_NAME` lookups are plain values. |
| `api.tls.existingSecret` | TLS on the frontend-to-api hop: a `kubernetes.io/tls` Secret with `ca.crt`, whose certificate names `<release>-api.<namespace>.svc.<clusterDomain>`. Only `ca.crt` reaches the frontend pods. |
| `networkPolicy.enabled` | Restricts the api to the frontend. Needs a CNI that enforces NetworkPolicy. |

`values.schema.json` checks every value at `helm install` time, so a typo or an unknown key
fails before anything is created. `helm show values deploy/helm/dataq` lists them all.

## Upgrade

The [upgrade procedure](backup-restore-upgrade.md#upgrading) applies; on Kubernetes the steps
are:

1. **Back up** the database.
2. **Read the release notes** for every release in between.
3. **Upgrade with the new release's chart**:

    ```bash
    helm upgrade dataq DataQ/deploy/helm/dataq -n dataq -f my-values.yaml --wait --timeout 10m
    ```

    The migrate hook runs first. If it fails, the upgrade fails and every Deployment keeps its
    previous version, so the running release keeps serving. The failed Job stays in the
    namespace for its logs (`kubectl -n dataq logs job/dataq-migrate`) and is replaced on the
    next attempt.
4. **Run the release's one-time steps**, then verify as after an install.

## Roll back

`helm rollback` re-deploys the previous images but does **not** downgrade the schema, and the
previous release's migration job cannot read a schema newer than itself. Follow the release's
**Rollback** line ([the doctrine](backup-restore-upgrade.md#rolling-back)):

1. Downgrade the schema with the **new** release's image, while it is still installed:

    ```bash
    kubectl -n dataq run dataq-downgrade --rm -i --restart=Never \
      --image=ghcr.io/theurgicduke771/dataq-backend:<new tag> \
      --overrides='{"spec":{"containers":[{"name":"dataq-downgrade","image":"ghcr.io/theurgicduke771/dataq-backend:<new tag>","workingDir":"/workspace/backend","command":["alembic","downgrade","<previous head>"],"envFrom":[{"configMapRef":{"name":"dataq-config"}},{"secretRef":{"name":"dataq-env"}}]}]}}'
    ```

2. `helm rollback dataq <revision> -n dataq`.
3. Verify: `/healthz`, sign-in, one suite run.

A release whose Rollback line says a database restore is needed is rolled back by restoring
the backup from step 1 of the upgrade instead.

## Test it on kind

[`deploy/helm/kind-smoke/smoke.sh`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/helm/kind-smoke)
installs the chart on a throwaway [kind](https://kind.sigs.k8s.io/) cluster with in-cluster
PostgreSQL, Redis, OpenBao and a mail catcher, then checks: the migration ran, every pod is
non-root on a read-only root filesystem, an emailed-code sign-in works through the frontend, a
suite run against a sample warehouse succeeds, the NetworkPolicy keeps other pods off the api,
an upgrade rolls, and a failing migration blocks the roll. It takes about ten minutes, most of
it pulling images, so it is a manual check rather than a CI job; CI lints and renders the chart
on every change.

```bash
deploy/helm/kind-smoke/smoke.sh          # KEEP=1 keeps the cluster afterwards
```
