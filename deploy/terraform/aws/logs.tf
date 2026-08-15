# CloudWatch Log Groups — one per service, matching the Azure stack's Log
# Analytics retention (log_retention_days). No APM/tracing wired up in this
# pass (the OTel core is vendor-neutral but the exporter shipped so far is
# Azure-only — see the approved plan's "not in scope" list); container
# stdout/stderr (structlog JSON) is the whole observability surface for now.

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
