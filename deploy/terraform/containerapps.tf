# The DataQ backend on Container Apps — api (external ingress), worker (Celery +
# embedded beat), and the migrate Job. All three run the SAME GHCR image
# (ghcr.io/theurgicduke771/dataq-backend:<tag>), pulled anonymously (public
# package, ADR 0023 — no registry block / credential).
#
# Boot-critical secrets (DATABASE_URL, App Insights) are inline Container App
# secrets, NOT KV references (see keyvault.tf for why). REDIS_URL is non-secret
# (in-environment, no auth). The app's runtime SecretStore reads datasource creds
# from Key Vault via the attached user-assigned identity (SECRET_STORE +
# AZURE_KEY_VAULT_URL).

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
    { name = "SAMPLE_FAILURES_RETENTION_DAYS", value = "30" },
    # Runtime SecretStore -> Key Vault via the user-assigned identity.
    { name = "SECRET_STORE", value = "azure_key_vault" },
    { name = "AZURE_KEY_VAULT_URL", value = azurerm_key_vault.app.vault_uri },
    # DefaultAzureCredential can't select a USER-assigned identity without being
    # told which one. Without this, every Key Vault read fails (no MI chosen) —
    # breaking secret resolution for connection tests, suite runs, and the
    # orchestration poll (#406). Must be the UAMI's client id, not principal id.
    { name = "AZURE_CLIENT_ID", value = azurerm_user_assigned_identity.app.client_id },
    # Real SSO in prod (AUTH_DEV_BYPASS=false). Client IDs come from the SSO app
    # registrations created in sso.tf; init_auth() validates v2 tokens against
    # AZURE_API_CLIENT_ID + AZURE_TENANT_ID.
    { name = "AUTH_DEV_BYPASS", value = "false" },
    { name = "AZURE_TENANT_ID", value = local.azure_tenant_id },
    { name = "AZURE_API_CLIENT_ID", value = azuread_application.api.client_id },
    { name = "AZURE_SPA_CLIENT_ID", value = azuread_application.spa.client_id },
    { name = "AZURE_API_SCOPE", value = var.azure_api_scope },
    # Guest (B2B / external) sign-in. Off by default (secure); this deployment
    # opts in via tfvars because the owner signs in with a guest account. When
    # off, the token validator rejects guests with 403 "Guest users not allowed".
    { name = "AZURE_ALLOW_GUEST_USERS", value = var.azure_allow_guest_users ? "true" : "false" },
    { name = "WORKSPACE_ADMIN_EMAILS", value = var.workspace_admin_emails },
    # Empty: the SWA linked backend proxies /api same-origin, so the FastAPI CORS
    # middleware stays off (README §4 / ADR 0018).
    { name = "CORS_ALLOW_ORIGINS", value = "" },
    # Public origin for the inbound-webhook URLs the admin webhook-config surface
    # generates (#490). The SWA host proxies /api same-origin, so an orchestrator
    # POSTing to <swa>/api/v1/orchestration/events/... reaches the api.
    { name = "PUBLIC_BASE_URL", value = "https://${azurerm_static_web_app.app.default_host_name}" },
    # Webhook secret KEY names (values live in Key Vault — keyvault.tf).
    { name = "ADF_WEBHOOK_SECRET_NAME", value = "adf-webhook-secret" },
    { name = "AIRFLOW_WEBHOOK_SECRET_NAME", value = "airflow-webhook-secret" },
    # Alerting channels (Slack + email) behind the ResultPublisher composite.
    # Secret NAMES point at Key Vault (the webhook URL / app-password live there);
    # SMTP coordinates are non-secret. Each publisher self-no-ops until its secret
    # + (for email) recipients are set, so leaving the email_* vars empty = email
    # off. Email addresses come from the gitignored tfvars (PII, not in git).
    { name = "SLACK_WEBHOOK_SECRET_NAME", value = "channel-slack-webhook" },
    { name = "EMAIL_SMTP_HOST", value = "smtp.gmail.com" },
    { name = "EMAIL_SMTP_PORT", value = "587" },
    { name = "EMAIL_PASSWORD_SECRET_NAME", value = "channel-email-password" },
    { name = "EMAIL_USERNAME", value = var.email_username },
    { name = "EMAIL_FROM", value = var.email_from },
    { name = "EMAIL_TO", value = var.email_to },
  ]
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

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
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

  tags       = local.common_tags
  depends_on = [azurerm_role_assignment.kv_app_reader]
}

# ── Worker (Celery worker + embedded beat) ───────────────────────────────────
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
    # min_replicas = 1: the worker also runs celery-beat (-B) for the schedule
    # dispatcher + orchestration polling, so it can't scale to zero.
    min_replicas = 1
    max_replicas = 1
    container {
      name    = "worker"
      image   = local.backend_image
      cpu     = 1.0
      memory  = "2Gi"
      command = ["celery", "-A", "backend.app.worker.celery_app", "worker", "-B", "--loglevel=INFO"]
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

  tags       = local.common_tags
  depends_on = [azurerm_role_assignment.kv_app_reader]
}

# ── Migrate Job (alembic upgrade head) ───────────────────────────────────────
# Manual-trigger job the Deploy workflow runs BEFORE rolling the apps (additive,
# backward-compatible migrations — CLAUDE.md). alembic.ini's script_location is
# relative to backend/, so cd there first (mirrors docker-compose's migrate svc).
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

  tags = local.common_tags
}
