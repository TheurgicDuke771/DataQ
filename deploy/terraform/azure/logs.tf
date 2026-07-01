# Log Analytics workspace — backs both the Container Apps environment (container
# logs) and the workspace-based Application Insights.

resource "azurerm_log_analytics_workspace" "app" {
  name                = "dataq-app-logs"
  location            = var.azure_location
  resource_group_name = data.azurerm_resource_group.dataq.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.common_tags
}
