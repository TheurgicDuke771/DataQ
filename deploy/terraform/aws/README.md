# DataQ on AWS — Terraform (OpenTofu) runbook

A second, parallel deployment of DataQ (Azure stays the deployed prod — see
`deploy/terraform/azure/`), into a fresh, dedicated AWS account. Unlike the
Azure stack, nothing here is shared with another stack: this account exists
only for this deployment.

## What it creates

| Resource | Purpose |
|---|---|
| VPC, 2 public subnets, IGW, route table | Networking. No NAT Gateway (decision: public-subnets-no-NAT, ~$33/mo saved — ECS tasks get no public inbound access, only the ALB→frontend path is internet-reachable) |
| `aws_ecs_cluster.app` + 3 Fargate services (api, worker, frontend) + 1 task def (migrate) | The app itself. api is internal-only (Cloud Map DNS `api.dataq.local`); frontend is the sole public surface (behind the ALB); worker runs embedded celery-beat, `desired_count=1` always (cannot scale to zero) |
| `aws_cloudfront_distribution.app` → `aws_lb.app` (ALB, HTTP :80) | Public ingress. CloudFront terminates HTTPS on its default `*.cloudfront.net` cert (#1345 — Cognito requires an HTTPS redirect URI); the ALB admits only CloudFront's origin-facing ranges and forwards to the frontend target group |
| `aws_db_instance.app` (RDS Postgres, `db.t4g.micro`) | The app's own database — this stack creates it directly (no shared-server bootstrap dance like the Azure stack, since this account is dedicated) |
| `aws_elasticache_replication_group.app` (`cache.t4g.micro`, TLS + auth token) | Celery broker + rate-limit store |
| `aws_cognito_user_pool.app` + SPA client | OIDC identity provider, validated by the backend's provider-neutral `OidcBearerScheme` (ADR 0026 amendment) — not Azure AD |
| `aws_secretsmanager_secret.*` (2–3, infra-owned) + IAM grants | `database-url`/`redis-url` (infra-owned, referenced by ARN in task defs) + the app's own runtime grant under `AWS_SECRETS_MANAGER_PREFIX` (`SECRET_STORE=aws_secrets_manager`) + when `alert_email` is set, `<prefix>/channel-email-password` (infra-written, app-read — the SES SMTP password, #1368) |
| `aws_ses_email_identity` + `aws_iam_user.ses_smtp` (+key/policy) — only when `alert_email` set | Email alert channel (#1368): SES identity (sandbox — sender = recipient = `alert_email`, needs the one-time verification click) + a send-only SMTP credential |
| `aws_iam_openid_connect_provider.github` + `aws_iam_role.github_deploy` | GitHub Actions → AWS auth for the Deploy workflow, OIDC federation, no stored access keys |
| CloudWatch Log Groups (4) | Container logs. No APM/tracing wired up yet — the OTel core is vendor-neutral but the exporter shipped so far is Azure-only |

## Prerequisites

- **OpenTofu** (`tofu`), not Terraform — same ADR 0024 rationale as the Azure stack.
- **AWS CLI credentials** for a **scoped IAM user**, never root. This repo's own bring-up used `AWS_PROFILE=dataq-deploy` (set as the default in the operator's shell) — see the session notes for the IAM bootstrap steps (create user, attach a policy, remove root's access keys/login session).
- A **GitHub repo** with Actions enabled (for the OIDC federation to have something to trust).
- Nothing else pre-exists — this stack creates its own VPC, database, cache, etc. from scratch, unlike the Azure stack's shared-resource dependencies.

## Apply

```bash
cd deploy/terraform/aws
tofu init
# state_encryption_passphrase comes from your terraform.tfvars — see
# terraform.tfvars.example.
#
# Image tags (#1349): the services RUN these images from the first apply.
# publish-images.yml tags main merges as `main-<full sha>` (the bare `<sha>`
# tag is owned by the manual Azure deploy.yml and usually does NOT exist), and
# its `paths` filter skips merges that don't change image content — so use the
# last IMAGE-BEARING main commit, not the branch tip, and verify the tag
# exists before applying:
IMG_SHA=$(git log origin/main -1 --format=%H -- backend frontend pyproject.toml)
docker manifest inspect "ghcr.io/theurgicduke771/dataq-backend:main-${IMG_SHA}" > /dev/null  # must succeed
TF_VAR_app_db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
tofu plan -input=false -out=tfplan \
  -var="image_tag=main-${IMG_SHA}" \
  -var="frontend_image_tag=main-${IMG_SHA}"
tofu apply -input=false tfplan
```

**Cost starts here.** `tofu apply` creates real, billable AWS resources (Fargate, RDS, ElastiCache, ALB). Review the plan before applying. See the root-level deployment plan (in the session that built this stack) for the cost breakdown against the AWS free-tier credit.

## After apply — wire the Deploy workflow

```bash
tofu output -raw github_deploy_role_arn   # → repo secret AWS_DEPLOY_ROLE_ARN
tofu output -raw ecs_cluster_name         # → repo variable ECS_CLUSTER
tofu output -raw frontend_url             # → sanity-check reachability
```

```bash
gh secret set AWS_DEPLOY_ROLE_ARN --body "$(tofu output -raw github_deploy_role_arn)"
gh variable set ECS_CLUSTER --body "$(tofu output -raw ecs_cluster_name)"
gh variable set AWS_REGION --body "$(tofu output -raw -json | jq -r .aws_region.value 2>/dev/null || echo us-east-2)"
```

The companion `.github/workflows/deploy-aws.yml` uses these to
`aws-actions/configure-aws-credentials` via OIDC, then runs the same sequence
the Azure `deploy.yml` runs, adapted to ECS:

1. build + push both images to GHCR under the immutable tag `aws-<commit sha>`
   (bare `<sha>` belongs to the Azure deploy.yml, `main-<sha>` to
   publish-images.yml — three workflows, three disjoint tag namespaces);
2. register a new `dataq-app-migrate` task-definition revision on the new
   image and `run-task` it with the api service's live network configuration,
   **waiting for exit 0** before anything rolls (fail-closed);
3. register new api/worker/frontend revisions, `update-service`, and
   `wait services-stable`, then verify the primary deployment's image really
   is the new tag (a circuit-breaker rollback to the old image must fail the
   run, not pass as "stable");
4. optional public-surface smoke (healthz/SPA 200, `/api` 401) — runs only
   when the `AWS_FRONTEND_URL` repo variable is set, since the CloudFront URL
   is deployment-specific and deliberately not tracked in the repo (#730).
   Set it with `gh variable set AWS_FRONTEND_URL --body "$(tofu output -raw frontend_url)"`.

It is `workflow_dispatch`-only (Azure stays primary prod), takes an optional
`image_tag` input (blank → `aws-<sha>`), and needs no AWS-side registry
credential — the GHCR packages are public and ECS pulls them anonymously.

## State encryption

Same discipline as the Azure stack (`versions.tf`'s `encryption {}` block):
the local state holds the generated RDS/ElastiCache passwords in plaintext
without it. `state_encryption_passphrase` is a **data-at-rest key**, not a
credential — it cannot be revoked or re-minted, and losing it makes
`terraform.tfstate` permanently unreadable. Keep a second copy off this
machine. A wrong/missing passphrase fails closed
(`cipher: message authentication failed`) without corrupting the file.

## Verify

```bash
# Confirm exactly what got created, tagged for this stack:
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Purpose,Values=dataq-app-aws

# Reachability (through the frontend — the sole public surface):
curl -s "$(tofu output -raw frontend_url)/healthz"
```

Then in a browser: sign in through Cognito end-to-end, confirm `/api` and
`/mcp` return 401 unauthenticated without a credential, and run one real
suite against a live datasource to confirm the worker path.

## Known gaps in this pass (deliberately deferred, not silently skipped)

- No custom domain — the public URL is the CloudFront distribution's own
  `*.cloudfront.net` domain (HTTPS, default cert — #1345). Once a domain is
  chosen: Route53 + ACM cert + an `aliases` entry on the distribution.
- No private-subnet/NAT hardening — see `main.tf`'s decision note.
- No `.github/workflows/deploy-aws.yml` yet — this stack only provisions the
  infra the workflow will drive.
- The Cognito `client_id`-vs-`aud` accommodation in `OidcBearerScheme`
  (backend) needs confirming against a real token from **this** pool once
  it's live — see `cognito.tf`'s comment.
