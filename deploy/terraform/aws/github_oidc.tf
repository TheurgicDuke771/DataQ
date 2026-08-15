# GitHub Actions -> AWS auth for the Deploy workflow — OIDC federation, no
# stored access keys. Mirrors deploy/terraform/azure/github_oidc.tf's shape:
# an OIDC identity provider trusting token.actions.githubusercontent.com, and
# a role whose trust policy conditions on the exact repo+environment subject.

# GitHub's OIDC token-signing certificate thumbprint. AWS's OIDC provider
# resource requires this argument, though AWS has stopped strictly validating
# it against the TLS chain for well-known providers — kept for API
# compatibility. Verify at apply time against
# https://token.actions.githubusercontent.com/.well-known/openid-configuration
# if GitHub ever rotates their intermediate CA.
locals {
  github_oidc_thumbprint = "6938fd4d98bab03faadb97b34396831e3780aea1"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [local.github_oidc_thumbprint]
}

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Must match the workflow's `environment: production` job scoping —
    # exactly the same shape as the Azure federated-credential subject.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "dataq-app-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
}

# Least privilege, resource-scoped to exactly the ECS objects the Deploy
# workflow touches (update-service / run-task for the migrate task) — never
# account-wide. iam:PassRole is scoped to only the task/execution roles this
# stack created, so the CI principal cannot pass an arbitrary role to a task.
data "aws_iam_policy_document" "github_deploy" {
  # Two statements, split by whether the action populates the `ecs:cluster`
  # condition key (#1348). Task-definition actions are not cluster-scoped and
  # never set the key, so putting them under the ArnEquals condition made the
  # condition evaluate against a MISSING key — an implicit deny that would
  # have failed the workflow's first `register-task-definition`.
  statement {
    sid = "EcsDeployClusterScoped"
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:RunTask",
      "ecs:DescribeTasks",
    ]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.app.arn]
    }
  }

  statement {
    sid = "EcsTaskDefinitions"
    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
    ]
    # `*` is the best available scoping: the task-definition action family
    # supports no resource-level restriction (registration creates a NEW
    # revision ARN). The blast radius stays contained by the PassTaskRoles
    # statement below — a registered definition is inert unless it can be run,
    # and running it needs a role this policy will only pass for this stack's
    # own task/execution roles, on this stack's cluster.
    resources = ["*"]
  }

  statement {
    sid     = "PassTaskRoles"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.ecs_task_execution.arn,
      aws_iam_role.ecs_task_api.arn,
      aws_iam_role.ecs_task_worker.arn,
    ]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "dataq-app-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
