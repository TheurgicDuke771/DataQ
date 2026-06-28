# Post-apply coordinates. The GitHub-config values feed the repo Secrets/Variables
# the Deploy workflow reads (see deploy/terraform/README.md for the gh commands).
# Sensitive values are marked so `terraform output -raw <name>` is needed to read.

output "api_url" {
  description = "Public API base URL (also the SWA linked-backend target)."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "swa_url" {
  description = "Static Web App URL (the product surface)."
  value       = "https://${azurerm_static_web_app.app.default_host_name}"
}

output "key_vault_url" {
  description = "Key Vault URI (AZURE_KEY_VAULT_URL the app's SecretStore uses)."
  value       = azurerm_key_vault.app.vault_uri
}

output "postgres_fqdn" {
  description = "App Postgres host."
  value       = azurerm_postgresql_flexible_server.app.fqdn
}

# ── Container Apps names -> Deploy workflow VARIABLES ─────────────────────────
output "resource_group" {
  description = "-> repo var AZURE_RESOURCE_GROUP"
  value       = data.azurerm_resource_group.dataq.name
}

output "api_app_name" {
  description = "-> repo var API_APP_NAME"
  value       = azurerm_container_app.api.name
}

output "worker_app_name" {
  description = "-> repo var WORKER_APP_NAME"
  value       = azurerm_container_app.worker.name
}

output "migrate_job_name" {
  description = "-> repo var MIGRATE_JOB_NAME"
  value       = azurerm_container_app_job.migrate.name
}

output "backend_image" {
  description = "GHCR image ref currently deployed."
  value       = local.backend_image
}

# ── GitHub OIDC -> Deploy workflow SECRETS ───────────────────────────────────
output "github_actions_client_id" {
  description = "-> repo secret AZURE_CLIENT_ID (the github-deploy app registration)."
  value       = azuread_application.github_deploy.client_id
}

output "azure_tenant_id" {
  description = "-> repo secret AZURE_TENANT_ID"
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "-> repo secret AZURE_SUBSCRIPTION_ID"
  value       = data.azurerm_subscription.current.subscription_id
}

# ── Sensitive ────────────────────────────────────────────────────────────────
output "swa_api_token" {
  description = "-> repo secret AZURE_STATIC_WEB_APPS_API_TOKEN (read with: terraform output -raw swa_api_token)."
  value       = azurerm_static_web_app.app.api_key
  sensitive   = true
}

output "appinsights_connection_string" {
  description = "App Insights connection string (injected into the apps; surfaced for reference)."
  value       = azurerm_application_insights.app.connection_string
  sensitive   = true
}
