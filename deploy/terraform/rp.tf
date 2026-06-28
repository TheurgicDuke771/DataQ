# Resource-provider registration. Microsoft.App / OperationalInsights /
# DBforPostgreSQL / ContainerRegistry are already Registered on this subscription
# (the harness needed them). These four are NotRegistered and the app stack needs
# them. Declarative + idempotent — Terraform no-ops once they're Registered.
# (Applying as Owner makes this possible; the harness's Contributor SP could not.)

resource "azurerm_resource_provider_registration" "web" {
  name = "Microsoft.Web" # Static Web App
}

resource "azurerm_resource_provider_registration" "keyvault" {
  name = "Microsoft.KeyVault"
}

resource "azurerm_resource_provider_registration" "insights" {
  name = "Microsoft.Insights" # Application Insights
}

resource "azurerm_resource_provider_registration" "cache" {
  name = "Microsoft.Cache" # reserved (managed Redis fallback); registered for parity
}
