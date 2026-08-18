# ECS Fargate — api (internal, Cloud Map DNS), worker (Celery + embedded
# beat, min=max=1 desired count — mirrors the Azure Container App's
# min_replicas=1 rationale: it can't scale to zero because it runs beat), and
# frontend (public via the ALB). All three run images from GHCR (public,
# anonymous pull — ADR 0023), same as the Azure stack.

resource "aws_ecs_cluster" "app" {
  name = "dataq-app"

  tags = { Name = "dataq-app" }
}

# One shared security group for every ECS task: self-referencing ingress lets
# api/worker/frontend talk to each other (and to Redis/RDS, which have their
# own SGs allowing traffic FROM this one), plus a narrow allow from the ALB
# SG for the frontend's published port.
resource "aws_security_group" "ecs_tasks" {
  name = "dataq-app-ecs-tasks"
  # EC2 SG descriptions allow ONLY a-zA-Z0-9. _-:/()#,@[]+=&;{}!$* (#1357) —
  # no em-dash, and no '>' either (the first fix missed that).
  description = "DataQ ECS tasks - internal task-to-task traffic + ALB to frontend"
  vpc_id      = aws_vpc.app.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "dataq-app-ecs-tasks" }
}

resource "aws_security_group_rule" "ecs_tasks_self_ingress" {
  # Narrower than "every port" on purpose: the only task-to-task call within
  # this security group is frontend -> api on 8000 (Cloud Map DNS). worker
  # and frontend serve nothing another task calls, so opening the full
  # 0-65535 range would grant blast-radius no actual traffic pattern needs.
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_tasks.id
  source_security_group_id = aws_security_group.ecs_tasks.id
}

resource "aws_security_group_rule" "ecs_tasks_from_alb" {
  type                     = "ingress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_tasks.id
  source_security_group_id = aws_security_group.alb.id
}

# Cloud Map private DNS namespace — how the frontend reaches the api
# internally (DATAQ_API_UPSTREAM=http://api.dataq.local:8000, alb.tf's
# local.api_internal_url), the same role the Azure environment's
# `.internal.<domain>` FQDN plays there.
resource "aws_service_discovery_private_dns_namespace" "app" {
  name = "dataq.local"
  vpc  = aws_vpc.app.id
}

resource "aws_service_discovery_service" "api" {
  name = "api"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.app.id
    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

locals {
  backend_image  = "${var.backend_image_repo}:${var.image_tag}"
  frontend_image = "${var.frontend_image_repo}:${var.frontend_image_tag}"

  # Non-secret env, shared by api + worker — mirrors containerapps.tf's
  # local.app_env, with AWS-shaped values in place of the Azure ones.
  app_env = [
    { name = "ENVIRONMENT", value = var.environment },
    # The jurisdiction this deployment DECLARES (G4/#434). Sourced from the same
    # variable that places the resources, so the declaration and the placement
    # cannot drift apart by editing one of them.
    { name = "DEPLOYMENT_REGION", value = var.aws_region },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "SAMPLE_FAILURES_RETENTION_DAYS", value = "30" },
    # Runtime SecretStore -> AWS Secrets Manager via the ECS task role (no
    # bootstrap credential to hold — same "no credential at all" posture the
    # Azure UAMI + Key Vault pairing has).
    { name = "SECRET_STORE", value = "aws_secrets_manager" },
    { name = "AWS_SECRETS_MANAGER_PREFIX", value = "${var.aws_secrets_manager_prefix}/" },
    # Real auth in prod (AUTH_DEV_BYPASS=false) via the generic OIDC path
    # (ADR 0026 amendment) — NOT the Azure fields, which stay unset.
    { name = "AUTH_DEV_BYPASS", value = "false" },
    { name = "OIDC_ISSUER", value = local.cognito_issuer },
    { name = "OIDC_AUDIENCE", value = aws_cognito_user_pool_client.spa.id },
    # App-side access gate (#1386) — second layer behind cognito.tf's
    # allow_admin_create_user_only. Empty = no gate (backend default, logged at
    # WARNING on boot); see variables.tf.
    { name = "OIDC_ALLOWED_EMAILS", value = var.oidc_allowed_emails },
    { name = "OIDC_ALLOWED_DOMAINS", value = var.oidc_allowed_domains },
    { name = "WORKSPACE_ADMIN_EMAILS", value = var.workspace_admin_emails },
    # Rate-limit per-IP keying (ADR 0035). Three proxies append to
    # X-Forwarded-For on the way in — CloudFront (appends the viewer IP), the
    # ALB (appends CloudFront's edge IP), and the frontend nginx
    # ($proxy_add_x_forwarded_for appends the ALB IP) — so the real client is
    # 3 entries from the right, same depth as the Azure stack's
    # envoy+nginx+envoy chain. Confirm against one logged live XFF
    # post-deploy, same as the Azure note.
    { name = "RATE_LIMIT_XFF_TRUSTED_HOPS", value = "3" },
    { name = "CORS_ALLOW_ORIGINS", value = "" },
    { name = "PUBLIC_BASE_URL", value = local.frontend_url },
    { name = "ADF_WEBHOOK_SECRET_NAME", value = "adf-webhook-secret" },
    { name = "AIRFLOW_WEBHOOK_SECRET_NAME", value = "airflow-webhook-secret" },
    { name = "DBT_WEBHOOK_SECRET_NAME", value = "dbt-webhook-secret" },
    { name = "SLACK_WEBHOOK_SECRET_NAME", value = "channel-slack-webhook" },
    # Spans + OTel logs → the ADOT sidecar on task-local loopback (#1369,
    # adot.tf) → X-Ray / CloudWatch. The app half is the vendor-neutral
    # core/otel.py (#589); this is just where its OTLP consumer lives.
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "http://localhost:4318" },
    # Email alert channel via SES (#1368, ses.tf) — everything derives from
    # var.alert_email; empty leaves the channel off (config.py's gate). The
    # SMTP password lives at dataq/channel-email-password in Secrets Manager,
    # read through the app's own SecretStore at send time.
    { name = "EMAIL_SMTP_HOST", value = local.ses_smtp_host },
    { name = "EMAIL_SMTP_PORT", value = "587" },
    { name = "EMAIL_PASSWORD_SECRET_NAME", value = "channel-email-password" },
    { name = "EMAIL_USERNAME", value = local.email_username },
    { name = "EMAIL_FROM", value = var.alert_email },
    { name = "EMAIL_TO", value = local.alert_email_to },
  ]

  worker_env = concat(local.app_env, [
    { name = "WAREHOUSE_LINEAGE_ENABLED", value = "true" },
  ])

  # Boot-critical secrets, injected via the task definition's `secrets` block
  # (execution-role-read from the infra-owned Secrets Manager entries) —
  # mirrors the Azure stack's inline-Container-App-secret pattern; same
  # reasoning (decouple first-revision activation from any IAM-propagation
  # delay on the app's OWN Secrets Manager grant).
  boot_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "REDIS_URL", valueFrom = aws_secretsmanager_secret.redis_url.arn },
  ]
}

# ── API (internal, Cloud Map) ────────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "dataq-app-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_api.arn

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = local.backend_image
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment  = local.app_env
      secrets      = local.boot_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    },
    # ADOT collector sidecar (#1369, adot.tf) — OTLP in on localhost:4318,
    # traces → X-Ray, OTel logs → CloudWatch. NOTE ignore_changes below:
    # adding/changing this on a LIVE stack needs `tofu apply -replace` on this
    # task definition + an `update-service` to the new revision (README,
    # "Rolling task-definition changes").
    local.adot_container["api"],
  ])

  # CI rolls the live image out-of-band (new task-def revision via
  # `aws ecs register-task-definition` + `update-service`) — mirrors the
  # Azure stack's `ignore_changes` on the container image, same reasoning.
  lifecycle {
    ignore_changes = [container_definitions]
  }

  tags = { Name = "dataq-app-api" }
}

resource "aws_ecs_service" "api" {
  name            = "dataq-app-api"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true # no NAT — see main.tf's decision note
  }

  service_registries {
    registry_arn = aws_service_discovery_service.api.arn
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

# ── Worker (Celery worker + embedded beat) ──────────────────────────────

resource "aws_ecs_task_definition" "worker" {
  family                   = "dataq-app-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_worker.arn

  container_definitions = jsonencode([
    {
      name        = "worker"
      image       = local.backend_image
      essential   = true
      command     = ["celery", "-A", "backend.app.worker.celery_app", "worker", "-B", "--loglevel=INFO"]
      environment = local.worker_env
      secrets     = local.boot_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
    # Same ADOT sidecar + same -replace rollout note as the api task def.
    local.adot_container["worker"],
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }

  tags = { Name = "dataq-app-worker" }
}

resource "aws_ecs_service" "worker" {
  name            = "dataq-app-worker"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.worker.arn
  # Cannot scale to zero — runs embedded celery-beat (schedule dispatcher +
  # orchestration polling), same as the Azure Container App's min_replicas=1.
  desired_count = 1
  launch_type   = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

# ── Frontend (nginx SPA, sole public surface) ───────────────────────────

resource "aws_ecs_task_definition" "frontend" {
  family                   = "dataq-app-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name         = "frontend"
      image        = local.frontend_image
      essential    = true
      portMappings = [{ containerPort = 8080, protocol = "tcp" }]
      environment = [
        { name = "DATAQ_API_UPSTREAM", value = local.api_internal_url },
        { name = "DATAQ_AUTH_MODE", value = "oidc" },
        { name = "DATAQ_AUTH_AUTHORITY", value = local.cognito_issuer },
        { name = "DATAQ_AUTH_CLIENT_ID", value = aws_cognito_user_pool_client.spa.id },
        # Scope override (#1347): Cognito has no offline_access scope and errors
        # (invalid_scope) on the SPA's default list; refresh tokens are issued
        # on the code grant regardless. Must match cognito.tf's
        # allowed_oauth_scopes.
        { name = "DATAQ_AUTH_SCOPE", value = "openid email profile" },
        # Sign-out dialect (#1364): Cognito's /logout is not RP-Initiated-Logout-
        # conformant — it needs client_id + logout_uri and 400s on the standard
        # id_token_hint/post_logout_redirect_uri, stranding the user on a raw
        # "Client does not exist" error page with the hosted-UI session alive.
        { name = "DATAQ_AUTH_LOGOUT_STYLE", value = "cognito" },
        # CSP connect-src tail (#1387). BOTH Cognito hosts are required and they
        # are different services: oidc-client-ts fetches discovery + JWKS from
        # the ISSUER host (cognito-idp.<region>.amazonaws.com) and then POSTs the
        # code exchange to the HOSTED-UI domain (<prefix>.auth.<region>.
        # amazoncognito.com). Omitting the second one yields a policy that passes
        # discovery and then blocks the token exchange — i.e. sign-in fails at
        # the last step, which is the least obvious way for this to break.
        {
          name = "DATAQ_CSP_CONNECT_SRC",
          value = join(" ", [
            "https://cognito-idp.${var.aws_region}.amazonaws.com",
            "https://${aws_cognito_user_pool_domain.app.domain}.auth.${var.aws_region}.amazoncognito.com",
          ])
        },
      ]
      # Origin-secret guard (#1355): nginx 403s any request not carrying the
      # header CloudFront stamps on origin fetches (cloudfront.tf). Injected
      # as a secret, not plaintext env. NOTE ignore_changes below: on a LIVE
      # stack a plain apply never registers this — roll it with
      # `tofu apply -replace` on this task definition + `update-service`
      # (README, "Rolling task-definition changes"), or the guard silently
      # stays fail-open while CloudFront stamps a header nobody checks.
      secrets = [
        { name = "DATAQ_ORIGIN_SECRET", valueFrom = aws_secretsmanager_secret.origin_secret.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.frontend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "frontend"
        }
      }
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }

  tags = { Name = "dataq-app-frontend" }
}

resource "aws_ecs_service" "frontend" {
  name            = "dataq-app-frontend"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 8080
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.http]
}
