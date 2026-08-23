# APM/tracing target (#1369) — the AWS counterpart of the Azure stack's App Insights export.

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/dataq-app/otel"
  retention_in_days = var.log_retention_days
  tags              = { Name = "dataq-app-otel" }
}

# The collector's OWN stdout (pipeline startup, export errors) — separate from
# the app logs so a collector failure is findable.
resource "aws_cloudwatch_log_group" "adot" {
  name              = "/dataq-app/adot"
  retention_in_days = var.log_retention_days
  tags              = { Name = "dataq-app-adot" }
}

# Task-role grant for what the two pipelines actually do.
data "aws_iam_policy_document" "adot" {
  statement {
    sid = "AdotXrayWrite"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }

  statement {
    sid = "AdotOtelLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:DescribeLogGroups",
    ]
    resources = [
      aws_cloudwatch_log_group.otel.arn,
      "${aws_cloudwatch_log_group.otel.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "adot_api" {
  name   = "dataq-app-adot"
  role   = aws_iam_role.ecs_task_api.id
  policy = data.aws_iam_policy_document.adot.json
}

resource "aws_iam_role_policy" "adot_worker" {
  name   = "dataq-app-adot"
  role   = aws_iam_role.ecs_task_worker.id
  policy = data.aws_iam_policy_document.adot.json
}

locals {
  # Pinned like every other image in this repo (the GX-pin rationale). Check
  # release notes before bumping: the collector's config schema drifts.
  adot_image = "public.ecr.aws/aws-observability/aws-otel-collector:v0.49.0"

  # One config per service so the CloudWatch log STREAM name distinguishes api-emitted from worker-
  # emitted OTel logs.
  adot_config = {
    for svc in ["api", "worker"] : svc => yamlencode({
      # Loopback, not 0.0.0.0: awsvpc mode shares one network namespace across the task's
      # containers, so localhost reaches the sidecar — and these tasks carry public IPs (no-NAT
      # design), so a wildcard bind would put an unauthenticated OTLP write endpoint one SG-rule
      # change away from the internet (PR #1371 review).
      receivers  = { otlp = { protocols = { http = { endpoint = "127.0.0.1:4318" } } } }
      processors = { batch = {} }
      exporters = {
        awsxray = {}
        awscloudwatchlogs = {
          log_group_name  = aws_cloudwatch_log_group.otel.name
          log_stream_name = svc
        }
      }
      service = {
        pipelines = {
          traces = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsxray"] }
          logs   = { receivers = ["otlp"], processors = ["batch"], exporters = ["awscloudwatchlogs"] }
        }
      }
    })
  }

  # Sidecar container definition, merged into the api/worker task defs in
  # ecs.tf. essential=false: a dead collector loses telemetry, never the app.
  adot_container = {
    for svc, config in local.adot_config : svc => {
      name      = "adot-collector"
      image     = local.adot_image
      essential = false
      environment = [
        { name = "AOT_CONFIG_CONTENT", value = config },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.adot.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = svc
        }
      }
    }
  }
}
