# Static Web App — hosts the React/Vite build. Standard SKU so the api Container
# App can be linked as a backend: SWA then proxies /api/* to it same-origin, which
# is exactly what frontend/staticwebapp.config.json + the relative axios baseURL
# ('/api/v1') expect (no CORS). The SPA content is uploaded by the Deploy workflow
# (Azure/static-web-apps-deploy) using the api token output below.

resource "azurerm_static_web_app" "app" {
  name                = "dataq-app-web"
  resource_group_name = data.azurerm_resource_group.dataq.name
  location            = var.azure_location
  sku_tier            = var.swa_sku
  sku_size            = var.swa_sku
  tags                = local.common_tags

  depends_on = [azurerm_resource_provider_registration.web]
}

# Link the api Container App as the SWA backend. No azurerm resource covers
# arbitrary linked backends (only the Functions registration), so use the CLI.
# `backends link` errors if a backend is already linked, so tolerate that on
# re-apply; re-run after changing the api by tainting this resource.
resource "null_resource" "swa_linked_backend" {
  triggers = {
    swa_id = azurerm_static_web_app.app.id
    api_id = azurerm_container_app.api.id
  }
  provisioner "local-exec" {
    command = <<-CMD
      az staticwebapp backends link \
        --name ${azurerm_static_web_app.app.name} \
        --resource-group ${data.azurerm_resource_group.dataq.name} \
        --backend-resource-id ${azurerm_container_app.api.id} \
        --backend-region ${var.azure_location} \
        --only-show-errors --output none || echo "backend already linked (or link pending) — continuing"
    CMD
  }
  depends_on = [azurerm_static_web_app.app, azurerm_container_app.api]
}
