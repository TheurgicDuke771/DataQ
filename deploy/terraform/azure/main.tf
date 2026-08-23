# Shared data sources, locals, and the idempotent RG ensure-step.

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

# ── Idempotent RG step ─────────────────────────────────────────────────────── `az group create` is
# idempotent: it creates dataq-rg if absent and is a no-op (with the same tags) if it already exist
resource "null_resource" "ensure_rg" {
  triggers = {
    rg       = var.azure_resource_group
    location = var.azure_location
  }
  provisioner "local-exec" {
    command = "az group create --name ${var.azure_resource_group} --location '${var.azure_location}' --tags project=dataq --only-show-errors --output none"
  }
}

data "azurerm_resource_group" "dataq" {
  name       = var.azure_resource_group
  depends_on = [null_resource.ensure_rg]
}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  # All DataQ-APP resources carry purpose=dataq-app so they're trivially distinguishable from any
  # other resources sharing the group in the shared RG (see the verification query in
  common_tags = {
    project = var.project_tag
    managed = "terraform"
    purpose = "dataq-app"
  }

  # Deterministic Container App FQDNs, derived from the shared environment's default domain rather
  # than each app's own ingress[0].fqdn.
  env_default_domain = data.azurerm_container_app_environment.shared.default_domain
  api_fqdn           = "dataq-app-api.internal.${local.env_default_domain}"
  frontend_fqdn      = "dataq-app-frontend.${local.env_default_domain}"
  api_internal_url   = "http://${local.api_fqdn}"
  frontend_url       = "https://${local.frontend_fqdn}"
}
