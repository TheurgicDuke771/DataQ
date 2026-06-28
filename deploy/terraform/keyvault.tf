# Key Vault — the app's runtime SecretStore (SECRET_STORE=azure_key_vault). It
# holds the datasource connection credentials the app writes/reads via the API at
# runtime, plus the pre-seeded orchestration webhook secrets. RBAC authorization
# (not access policies): the UAMI gets Secrets User (read); the deployer gets
# Secrets Officer (write) so Terraform can seed the webhook secrets.
#
# NOTE: boot-critical config (DATABASE_URL / REDIS_URL / App Insights) is injected
# as inline Container App secrets in containerapps.tf, NOT via KV references — that
# decouples first-revision activation from KV-RBAC propagation delay (the classic
# "secret ref fails on the very first apply" gotcha). The vault is still exercised
# end-to-end by the UAMI read path + the webhook secrets below.

resource "azurerm_key_vault" "app" {
  name                       = "dataq-app-kv-${random_string.suffix.result}"
  location                   = var.azure_location
  resource_group_name        = data.azurerm_resource_group.dataq.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  tags                       = local.common_tags

  depends_on = [azurerm_resource_provider_registration.keyvault]
}

# Deployer (Owner user running this apply) -> write secrets.
resource "azurerm_role_assignment" "kv_deployer" {
  scope                = azurerm_key_vault.app.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# App identity -> read secrets at runtime (DefaultAzureCredential).
resource "azurerm_role_assignment" "kv_app_reader" {
  scope                = azurerm_key_vault.app.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# RBAC data-plane role assignments are eventually consistent — wait before the
# first secret write so Terraform doesn't 403 immediately after the grant.
resource "time_sleep" "kv_rbac_propagation" {
  create_duration = "60s"
  depends_on      = [azurerm_role_assignment.kv_deployer]
}

# ── Pre-seeded webhook secrets (orchestration event auth) ────────────────────
# Names match deploy/.env.app.prod.example (ADF_WEBHOOK_SECRET_NAME etc.). Values
# are generated; rotate via the provider rotation path (ADR 0006/0007).

resource "random_password" "adf_webhook" {
  length  = 40
  special = false
}

resource "random_password" "airflow_webhook" {
  length  = 40
  special = false
}

resource "azurerm_key_vault_secret" "adf_webhook" {
  name         = "adf-webhook-secret"
  value        = random_password.adf_webhook.result
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [time_sleep.kv_rbac_propagation]
}

resource "azurerm_key_vault_secret" "airflow_webhook" {
  name         = "airflow-webhook-secret"
  value        = random_password.airflow_webhook.result
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [time_sleep.kv_rbac_propagation]
}
