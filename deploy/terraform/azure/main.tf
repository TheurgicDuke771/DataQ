# Shared data sources, locals, and the resource group (created, or referenced when
# create_shared_resources = false).

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

resource "azurerm_resource_group" "dataq" {
  count    = var.create_shared_resources ? 1 : 0
  name     = var.azure_resource_group
  location = var.azure_location
  tags     = { project = var.project_tag }
}

data "azurerm_resource_group" "dataq" {
  count = var.create_shared_resources ? 0 : 1
  name  = var.azure_resource_group
}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  resource_group_name = one(concat(
    azurerm_resource_group.dataq[*].name, data.azurerm_resource_group.dataq[*].name
  ))

  # All DataQ-APP resources carry purpose=dataq-app so they're trivially distinguishable from any
  # other resources sharing the group in the shared RG (see the verification query in
  # deploy/terraform/README.md).
  common_tags = {
    project = var.project_tag
    managed = "terraform"
    purpose = "dataq-app"
  }

  # Deterministic Container App FQDNs, derived from the shared environment's default domain rather
  # than each app's own ingress[0].fqdn.
  env_default_domain = local.container_app_environment_default_domain
  api_fqdn           = "dataq-app-api.internal.${local.env_default_domain}"
  frontend_fqdn      = "dataq-app-frontend.${local.env_default_domain}"
  api_internal_url   = "http://${local.api_fqdn}"
  frontend_url       = "https://${local.frontend_fqdn}"
}
