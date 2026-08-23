# ECS task execution role (pulls images, writes to CloudWatch Logs — the infra-level identity) and
# per-service task roles (what the APP code itself assumes at runtime.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "dataq-app-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# GHCR images are public (anonymous pull, ADR 0023) — no ECR permissions needed.
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# One task role per service that needs AWS API access at runtime. `frontend`
# (static nginx) gets none — it never calls an AWS API.
resource "aws_iam_role" "ecs_task_api" {
  name               = "dataq-app-ecs-task-api"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "ecs_task_worker" {
  name               = "dataq-app-ecs-task-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}
