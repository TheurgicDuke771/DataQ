# Container Apps environment — the shared host for the api, worker, redis broker,
# and the migrate job. Separate from the harness's dataq-harness-cae.

resource "azurerm_container_app_environment" "app" {
  name = "dataq-app-cae"
  # westus3, not azure_location: 1-env-per-region cap + harness owns the westus2
  # slot (see var.aca_location).
  location                   = var.aca_location
  resource_group_name        = data.azurerm_resource_group.dataq.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.app.id
  tags                       = local.common_tags
  # Microsoft.App is already Registered on this subscription (the harness needs
  # it), so no RP dependency is required here.
}
