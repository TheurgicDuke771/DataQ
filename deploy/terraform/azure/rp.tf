# Resource-provider registration.

# Microsoft.Web was needed by the Static Web App (removed in the ADR 0028 §5 cutover — the frontend
# now runs on Container Apps / Microsoft.App, already registered).
resource "azurerm_resource_provider_registration" "web" {
  name = "Microsoft.Web"
}

resource "azurerm_resource_provider_registration" "keyvault" {
  name = "Microsoft.KeyVault"
}

resource "azurerm_resource_provider_registration" "insights" {
  # The azurerm provider's known-RP list spells this one lowercase
  # ("microsoft.insights"); the resource name match is case-sensitive.
  name = "microsoft.insights" # Application Insights
}

resource "azurerm_resource_provider_registration" "cache" {
  name = "Microsoft.Cache" # reserved (managed Redis fallback); registered for parity
}
