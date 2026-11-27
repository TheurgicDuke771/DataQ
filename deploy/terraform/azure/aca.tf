# The Container Apps environment: created, or referenced when create_shared_resources = false (a
# free/trial subscription allows ONE, which other workloads may already use, ADR 0024).
resource "azurerm_container_app_environment" "shared" {
  count                      = var.create_shared_resources ? 1 : 0
  name                       = var.container_app_environment_name
  location                   = var.azure_location
  resource_group_name        = local.resource_group_name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.app.id
  tags                       = local.common_tags
}

locals {
  # One of the two exists; one() reads it from whichever list is not empty.
  container_app_environment_id = one(concat(
    azurerm_container_app_environment.shared[*].id,
    data.azurerm_container_app_environment.shared[*].id,
  ))
  container_app_environment_location = one(concat(
    azurerm_container_app_environment.shared[*].location,
    data.azurerm_container_app_environment.shared[*].location,
  ))
  container_app_environment_default_domain = one(concat(
    azurerm_container_app_environment.shared[*].default_domain,
    data.azurerm_container_app_environment.shared[*].default_domain,
  ))
}

data "azurerm_container_app_environment" "shared" {
  count               = var.create_shared_resources ? 0 : 1
  name                = var.container_app_environment_name
  resource_group_name = local.resource_group_name

  # ── Residency assertion (G4 / #434) ──────────────────────────────────────── This environment is
  # SHARED and declared as a `data` source, so its region is whatever someone else created it in.
  lifecycle {
    postcondition {
      condition     = lower(replace(self.location, " ", "")) == lower(replace(var.azure_location, " ", ""))
      error_message = <<-EOT
        Residency mismatch (G4/#434): the shared Container Apps environment is in
        '${self.location}' but var.azure_location declares '${var.azure_location}'.
        Every Container App and Job in this stack inherits the ENVIRONMENT's region,
        so applying would place the app's compute outside the declared jurisdiction.
        There is deliberately no override for this one (unlike the shared DB's
        check): the environment's region is inherited by every Container App and
        Job here, so accepting a mismatch would relocate the whole deployment.
        Either move the workload back, or change azure_location deliberately.
      EOT
    }
  }
}
