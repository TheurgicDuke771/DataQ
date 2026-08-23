# Migrate task (alembic upgrade head) — a discrete one-shot task definition, no ECS service.

resource "aws_ecs_task_definition" "migrate" {
  family                   = "dataq-app-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = local.backend_image
      essential = true
      command   = ["sh", "-c", "cd backend && alembic upgrade head"]
      secrets   = local.boot_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migrate.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "migrate"
        }
      }
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }

  tags = { Name = "dataq-app-migrate" }
}
