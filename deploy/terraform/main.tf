# Shared data sources, locals, and the idempotent RG ensure-step.

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

# ── Idempotent RG step ───────────────────────────────────────────────────────
# `az group create` is idempotent: it creates dataq-rg if absent and is a no-op
# (with the same tags) if it already exists. We only ever pass project=dataq so
# the shared RG's existing tag is preserved and the harness resources in the same
# RG are unaffected. The data source below then references the (now-guaranteed) RG
# — we never manage/destroy the shared RG as a Terraform resource.
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
  # All DataQ-APP resources carry purpose=dataq-app so they're trivially
  # distinguishable from the harness's purpose=dataq-harness resources in the
  # shared RG (see the verification query in deploy/terraform/README.md).
  common_tags = {
    project = var.project_tag
    managed = "terraform"
    purpose = "dataq-app"
  }

  # Deterministic Container App FQDNs, derived from the shared environment's
  # default domain rather than each app's own ingress[0].fqdn. This is what breaks
  # the frontend<->api circular dependency (ADR 0028 §5 cutover): the frontend
  # needs the api's URL as its proxy upstream, and the api needs the frontend's URL
  # for PUBLIC_BASE_URL + the SPA redirect. An external-ingress app is reachable at
  # `https://<app-name>.<env-default-domain>`, so both URLs are computable up front
  # from the (read-only) environment data source — no resource references crossing.
  env_default_domain = data.azurerm_container_app_environment.shared.default_domain
  api_fqdn           = "dataq-app-api.${local.env_default_domain}"
  frontend_fqdn      = "dataq-app-frontend.${local.env_default_domain}"
  api_public_url     = "https://${local.api_fqdn}"
  frontend_url       = "https://${local.frontend_fqdn}"
}
