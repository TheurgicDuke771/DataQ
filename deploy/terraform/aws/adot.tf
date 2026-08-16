# APM/tracing target (#1369) — the AWS counterpart of the Azure stack's App
# Insights export. The APPLICATION half already exists and is vendor-neutral:
# core/otel.py (#589) exports spans AND logs to any OTEL_EXPORTER_OTLP_ENDPOINT
# via opentelemetry-exporter-otlp-proto-http. This file supplies the consumer:
# an ADOT collector sidecar in the api + worker tasks receiving OTLP on
# localhost:4318 and shipping traces → X-Ray, logs → CloudWatch.
#
# Config is injected via AOT_CONFIG_CONTENT (an ADOT-supported env override)
# instead of the image's /etc/ecs/ecs-default-config.yaml: the default config
# also runs statsd + EMF-metrics pipelines this deployment doesn't use, and a
# pipeline we run is a pipeline we must grant IAM for — a minimal config keeps
# the task-role grant minimal. The logs pipeline is NOT optional: otel.py
# builds a log exporter from the same endpoint, and a collector with no logs
# pipeline 404s /v1/logs — a repeating export failure is exactly the #852
# exporter-noise-loop shape.
#
# X-Ray accepts W3C/random OTel trace ids (AWS added support in 2023), so the
# app needs no X-Ray-specific id generator.

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

# Task-role grant for what the two pipelines actually do. X-Ray's write
# actions support no resource-level scoping (SAR: PutTraceSegments etc. are
# resources=*); the logs half is scoped to the one group the config names.
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

  # One config per service so the CloudWatch log STREAM name distinguishes
  # api-emitted from worker-emitted OTel logs. Region comes from the task's
  # own AWS_REGION/metadata — nothing hardcoded.
  adot_config = {
    for svc in ["api", "worker"] : svc => yamlencode({
      receivers  = { otlp = { protocols = { http = { endpoint = "0.0.0.0:4318" } } } }
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
