# Secrets Manager has two distinct owners, matching the Key Vault split on the
# Azure stack (boot-critical secrets are inline Container App secrets there,
# NOT Key Vault references — same reasoning applies here):
#
#   - Infra-owned bootstrap secrets (DATABASE_URL, REDIS_URL) — created by
#     THIS Terraform stack, referenced directly by ARN in each ECS task
#     definition's `secrets` block. Under `dataq-app-infra/`.
#   - App-owned datasource-credential secrets — written by the app itself at
#     runtime via `AwsSecretsManagerStore` (SECRET_STORE=aws_secrets_manager,
#     backend/app/core/secrets.py), under `var.aws_secrets_manager_prefix`
#     ("dataq/" by default). This stack only grants the IAM policy; it never
#     creates or reads these values.

resource "aws_secretsmanager_secret" "database_url" {
  name = "dataq-app-infra/database-url"
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = local.database_url
}

resource "aws_secretsmanager_secret" "redis_url" {
  name = "dataq-app-infra/redis-url"
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id     = aws_secretsmanager_secret.redis_url.id
  secret_string = local.redis_url
}

# The app's own runtime grant: get/put/create/delete/list on everything under
# its prefix, plus ListSecrets (unavoidably unscoped — the List action family
# has no resource-level restriction in Secrets Manager; AwsSecretsManagerStore
# narrows this itself with a strict client-side prefix check — see
# backend/app/core/secrets.py's list_secrets docstring).
data "aws_iam_policy_document" "app_secrets_rw" {
  statement {
    sid = "AppSecretsReadWrite"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.aws_secrets_manager_prefix}/*"
    ]
  }
  statement {
    sid       = "AppSecretsList"
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api_app_secrets" {
  name   = "dataq-app-secrets-rw"
  role   = aws_iam_role.ecs_task_api.id
  policy = data.aws_iam_policy_document.app_secrets_rw.json
}

resource "aws_iam_role_policy" "worker_app_secrets" {
  name   = "dataq-app-secrets-rw"
  role   = aws_iam_role.ecs_task_worker.id
  policy = data.aws_iam_policy_document.app_secrets_rw.json
}

# Read-only grant on the infra-owned bootstrap secrets, for the ECS task
# EXECUTION role — this is what actually injects them into the container via
# the task definition's `secrets` block (distinct from the task role above,
# which is the app's own runtime identity).
data "aws_iam_policy_document" "execution_infra_secrets_read" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.redis_url.arn,
      aws_secretsmanager_secret.origin_secret.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_infra_secrets_read" {
  name   = "dataq-app-infra-secrets-read"
  role   = aws_iam_role.ecs_task_execution.name
  policy = data.aws_iam_policy_document.execution_infra_secrets_read.json
}
