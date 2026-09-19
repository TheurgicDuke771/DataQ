# The DataQ backend on Container Apps — api (external ingress), worker (Celery, no beat — #1811),
# beat (the sole schedule dispatcher, its own Container App), and the migrate Job.

locals {
  backend_image   = "${var.backend_image_repo}:${var.image_tag}"
  azure_tenant_id = var.azure_tenant_id != "" ? var.azure_tenant_id : data.azurerm_client_config.current.tenant_id

  # Inline secrets shared by api + worker (+ DATABASE_URL also on the migrate job).
  app_secrets = [
    { name = "database-url", value = local.database_url },
    { name = "redis-url", value = local.redis_url },
    { name = "appinsights-conn", value = azurerm_application_insights.app.connection_string },
  ]

  # Non-secret env + secret_name references, shared by api + worker.
  app_env = [
    { name = "ENVIRONMENT", value = var.environment },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "DATABASE_URL", secret_name = "database-url" },
    { name = "REDIS_URL", secret_name = "redis-url" },
    { name = "APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = "appinsights-conn" },
    # The jurisdiction this deployment DECLARES (G4/#434).
    { name = "DEPLOYMENT_REGION", value = var.azure_location },
    { name = "SAMPLE_FAILURES_RETENTION_DAYS", value = "30" },
    # Runtime SecretStore -> Key Vault via the user-assigned identity.
    { name = "SECRET_STORE", value = "azure_key_vault" },
    { name = "AZURE_KEY_VAULT_URL", value = azurerm_key_vault.app.vault_uri },
    # DefaultAzureCredential can't select a USER-assigned identity without being told which one.
    { name = "AZURE_CLIENT_ID", value = azurerm_user_assigned_identity.app.client_id },
    # Real SSO in prod (AUTH_DEV_BYPASS=false).
    { name = "AUTH_DEV_BYPASS", value = "false" },
    { name = "AZURE_TENANT_ID", value = local.azure_tenant_id },
    { name = "AZURE_API_CLIENT_ID", value = azuread_application.api.client_id },
    { name = "AZURE_SPA_CLIENT_ID", value = azuread_application.spa.client_id },
    { name = "AZURE_API_SCOPE", value = var.azure_api_scope },
    # Guest (B2B / external) sign-in.
    { name = "AZURE_ALLOW_GUEST_USERS", value = var.azure_allow_guest_users ? "true" : "false" },
    { name = "WORKSPACE_ADMIN_EMAILS", value = var.workspace_admin_emails },
    # Rate-limit per-IP keying (ADR 0035).
    { name = "RATE_LIMIT_XFF_TRUSTED_HOPS", value = "3" },
    # Empty: the frontend Container App proxies /api same-origin (nginx), so the
    # FastAPI CORS middleware stays off (README §4 / ADR 0018 / ADR 0028).
    { name = "CORS_ALLOW_ORIGINS", value = "" },
    # Public origin for the inbound-webhook URLs the admin webhook-config surface generates (#490).
    { name = "PUBLIC_BASE_URL", value = local.frontend_url },
    # Webhook secret KEY names (values live in Key Vault — keyvault.tf).
    { name = "ADF_WEBHOOK_SECRET_NAME", value = "adf-webhook-secret" },
    { name = "AIRFLOW_WEBHOOK_SECRET_NAME", value = "airflow-webhook-secret" },
    { name = "DBT_WEBHOOK_SECRET_NAME", value = "dbt-webhook-secret" },
    # Alerting channels (Slack + email) behind the ResultPublisher composite.
    { name = "SLACK_WEBHOOK_SECRET_NAME", value = "channel-slack-webhook" },
    { name = "EMAIL_SMTP_HOST", value = "smtp.gmail.com" },
    { name = "EMAIL_SMTP_PORT", value = "587" },
    { name = "EMAIL_PASSWORD_SECRET_NAME", value = "channel-email-password" },
    { name = "EMAIL_USERNAME", value = var.email_username },
    { name = "EMAIL_FROM", value = var.email_from },
    { name = "EMAIL_TO", value = var.email_to },
  ]

  # Worker + beat env, on top of app_env (beat also gets it — same boot-time SecretStore/DB wiring,
  # #1811 — even though WAREHOUSE_LINEAGE_ENABLED only matters to the worker's sweep task).
  worker_env = concat(local.app_env, [
    { name = "WAREHOUSE_LINEAGE_ENABLED", value = "true" },
  ])
}

# ── API (FastAPI, external ingress) ──────────────────────────────────────────
resource "azurerm_container_app" "api" {
  name                         = "dataq-app-api"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dynamic "secret" {
    for_each = local.app_secrets
    content {
      name  = secret.value.name
      value = secret.value.value
    }
  }

  # INTERNAL ingress (ADR 0028 §5): the frontend Container App is the sole public surface and
  # reverse-proxies /api + /mcp to this app over the in-environment endpoint.
  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "auto"
    # Accept plain HTTP on the internal endpoint.
    allow_insecure_connections = true
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3
    container {
      name   = "api"
      image  = local.backend_image
      cpu    = 0.5
      memory = "1Gi"
      # Image CMD already runs `uvicorn backend.app.main:app --host 0.0.0.0
      # --port 8000` (no --reload in the image), so no command override.
      dynamic "env" {
        for_each = local.app_env
        content {
          name        = env.value.name
          value       = lookup(env.value, "value", null)
          secret_name = lookup(env.value, "secret_name", null)
        }
      }
    }
  }

  # The Deploy workflow rolls images out-of-band (`az containerapp update --image <sha>`), so the
  # live image is ahead of var.image_tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  tags       = local.common_tags
  depends_on = [azurerm_role_assignment.kv_app_secrets]
}

# ── Worker (Celery worker — task execution only, no beat since #1811) ───────
resource "azurerm_container_app" "worker" {
  name                         = "dataq-app-worker"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dynamic "secret" {
    for_each = local.app_secrets
    content {
      name  = secret.value.name
      value = secret.value.value
    }
  }

  template {
    # min_replicas = 1: NOT beat's reason anymore (#1811 moved beat to its own app below) — kept
    # at 1 so a queued run_suite/llm_invoke doesn't wait on a cold-start replica before it's even
    # picked up. Re-evaluate scale-to-zero separately if dispatch latency is ever found acceptable.
    min_replicas = 1
    max_replicas = 1
    container {
      name   = "worker"
      image  = local.backend_image
      cpu    = 1.0
      memory = "2Gi"
      # -Q celery,llm (#1777): llm_invoke has its own queue now — must be listed or this worker
      # never consumes it. NO -B (#1811): beat is a separate Container App below, so a worker OOM
      # (overlapping large suites, #1790) can never take the scheduler with it. Pool size is not a
      # flag here — WORKER_CONCURRENCY ships with the image.
      command = ["celery", "-A", "backend.app.worker.celery_app", "worker", "-Q", "celery,llm", "--loglevel=INFO"]
      dynamic "env" {
        for_each = local.worker_env
        content {
          name        = env.value.name
          value       = lookup(env.value, "value", null)
          secret_name = lookup(env.value, "secret_name", null)
        }
      }
    }
  }

  # Image is workflow-managed (see the api resource) — ignore it so an apply never
  # rolls the worker back to var.image_tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  tags       = local.common_tags
  depends_on = [azurerm_role_assignment.kv_app_secrets]
}

# ── Beat (Celery schedule dispatcher — ONLY this one, #1811) ─────────────────
# Split out of the worker so a worker OOM (prefork children under the 2 GiB hard limit,
# overlapping large suites — #1790) can never take the scheduler down with it (the #405
# class: orchestration polling, scheduled-suite dispatch, and every sweep going silently dark).
# min = max = 1: beat must run EXACTLY ONE instance or every periodic task fires twice.
resource "azurerm_container_app" "beat" {
  name                         = "dataq-app-beat"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dynamic "secret" {
    for_each = local.app_secrets
    content {
      name  = secret.value.name
      value = secret.value.value
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name  = "beat"
      image = local.backend_image
      # Beat only schedules — it enqueues tasks, never runs them — so it carries the platform's
      # smallest valid CPU/memory pairing, nowhere near the worker's 1.0/2Gi.
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["celery", "-A", "backend.app.worker.celery_app", "beat", "--loglevel=INFO"]
      # Same env as the worker (local.worker_env, not just app_env): beat needs the same
      # SECRET_STORE/DATABASE_URL/APPLICATIONINSIGHTS wiring to boot cleanly, even though it never
      # reads a connection credential itself.
      dynamic "env" {
        for_each = local.worker_env
        content {
          name        = env.value.name
          value       = lookup(env.value, "value", null)
          secret_name = lookup(env.value, "secret_name", null)
        }
      }
    }
  }

  # Image is workflow-managed (see the api resource) — ignore it so an apply never
  # rolls beat back to var.image_tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  tags       = local.common_tags
  depends_on = [azurerm_role_assignment.kv_app_secrets]
}

# ── Migrate Job (alembic upgrade head) ─────────────────────────────────────── Manual-trigger job
# the Deploy workflow runs BEFORE rolling the apps (additive, backward-compatible migrations —
# CLAUDE.md). alembic.ini's script_location is relative to backend/, so cd there first (mirrors
# docker-compose's migrate svc).
resource "azurerm_container_app_job" "migrate" {
  name                         = "dataq-app-migrate"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  # A Container Apps Job must be in the same region as its (shared) environment.
  location = data.azurerm_container_app_environment.shared.location

  replica_timeout_in_seconds = 900
  replica_retry_limit        = 1

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  template {
    container {
      name    = "migrate"
      image   = local.backend_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["sh", "-c", "cd backend && alembic upgrade head"]
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
    }
  }

  # Image is workflow-managed (see the api resource) — ignore it so an apply never
  # rolls the migrate job back to var.image_tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  tags = local.common_tags
}
