# Application Insights (workspace-based) — the observability sink the backend's
# structlog/App-Insights wiring posts to via APPLICATIONINSIGHTS_CONNECTION_STRING
# (injected as a Container App secret in containerapps.tf).

resource "azurerm_application_insights" "app" {
  name                = "dataq-app-ai"
  location            = var.azure_location
  resource_group_name = data.azurerm_resource_group.dataq.name
  workspace_id        = azurerm_log_analytics_workspace.app.id
  application_type    = "web"
  tags                = local.common_tags

  depends_on = [azurerm_resource_provider_registration.insights]
}
