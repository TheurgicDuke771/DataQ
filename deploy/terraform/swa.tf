# Static Web App — hosts the React/Vite build. Standard SKU so the api Container
# App can be linked as a backend: SWA then proxies /api/* to it same-origin, which
# is exactly what frontend/public/staticwebapp.config.json + the relative axios baseURL
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
#
# `backends link` errors if *any* backend is already linked, so we can't just
# `|| true` — that would also mask a real failure (bad region, missing
# permission) AND still record the trigger hash as applied, so the broken link
# is reported as success and never retried (#396). Instead: check the current
# link state first and skip only when our api is already the linked backend;
# any other failure from `link` propagates (set -e, no swallow). Re-run after
# changing the api by tainting this resource.
resource "null_resource" "swa_linked_backend" {
  triggers = {
    swa_id = azurerm_static_web_app.app.id
    api_id = azurerm_container_app.api.id
  }
  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-CMD
      set -euo pipefail
      # Substring-match the api's resource id against the raw `show` output rather
      # than a JMESPath: the exact JSON shape of `backends show` (array vs object,
      # flattened vs `properties`-nested) is version-dependent, but the full
      # resource id is unique enough that finding it anywhere means it's linked.
      # `|| true` covers the no-backend-linked case (empty/non-zero) without
      # masking the link step below. A *different* backend linked (only one is
      # allowed) won't match, so we fall through to link and that fails loudly.
      linked=$(az staticwebapp backends show \
        --name ${azurerm_static_web_app.app.name} \
        --resource-group ${data.azurerm_resource_group.dataq.name} \
        --only-show-errors -o json || true)
      if printf '%s' "$linked" | grep -qF "${azurerm_container_app.api.id}"; then
        echo "api already linked as the SWA backend — skipping"
        exit 0
      fi
      az staticwebapp backends link \
        --name ${azurerm_static_web_app.app.name} \
        --resource-group ${data.azurerm_resource_group.dataq.name} \
        --backend-resource-id ${azurerm_container_app.api.id} \
        --backend-region ${data.azurerm_container_app_environment.shared.location} \
        --only-show-errors --output none
    CMD
  }
  depends_on = [azurerm_static_web_app.app, azurerm_container_app.api]
}
