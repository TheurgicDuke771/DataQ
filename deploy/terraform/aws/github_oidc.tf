# GitHub Actions -> AWS auth for the Deploy workflow — OIDC federation, no stored access keys.

# GitHub's OIDC token-signing certificate thumbprint.
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

# Least privilege, resource-scoped to exactly the ECS objects the Deploy workflow touches (update-
# service / run-task for the migrate task).
data "aws_iam_policy_document" "github_deploy" {
  # Two statements, split by whether the action populates the `ecs:cluster` condition key (#1348).
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
    sid     = "EcsRegisterTaskDefinitions"
    actions = ["ecs:RegisterTaskDefinition"]
    # Family-scoped: RegisterTaskDefinition DOES support resource-level permissions (SAR resource
    # type `task-definition*` — /code-review finding).
    resources = [
      "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/dataq-app-*"
    ]
  }

  statement {
    sid     = "EcsDescribeTaskDefinitions"
    actions = ["ecs:DescribeTaskDefinition"]
    # `*` is genuinely the only option here: DescribeTaskDefinition supports
    # neither resource-level permissions nor any condition key.
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
