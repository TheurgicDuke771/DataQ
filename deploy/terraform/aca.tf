# Container Apps environment — the shared host for the api, worker, redis broker,
# and the migrate job. Separate from the harness's dataq-harness-cae.

resource "azurerm_container_app_environment" "app" {
  name                       = "dataq-app-cae"
  location                   = var.azure_location
  resource_group_name        = data.azurerm_resource_group.dataq.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.app.id
  tags                       = local.common_tags
  # Microsoft.App is already Registered on this subscription (the harness needs
  # it), so no RP dependency is required here.
}
