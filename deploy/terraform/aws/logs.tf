# CloudWatch Log Groups — one per service, matching the Azure stack's Log Analytics retention
# (log_retention_days).

resource "aws_cloudwatch_log_group" "api" {
  name              = "/dataq-app/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/dataq-app/worker"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/dataq-app/frontend"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/dataq-app/migrate"
  retention_in_days = var.log_retention_days
}
