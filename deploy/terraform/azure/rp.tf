# Resource-provider registration. Microsoft.App / OperationalInsights /
# DBforPostgreSQL / ContainerRegistry are already Registered on this subscription
# (the harness needed them). These four are NotRegistered and the app stack needs
# them. Declarative + idempotent — Terraform no-ops once they're Registered.
# (Applying as Owner makes this possible; the harness's Contributor SP could not.)

# Microsoft.Web was needed by the Static Web App (removed in the ADR 0028 §5
# cutover — the frontend now runs on Container Apps / Microsoft.App, already
# registered). Kept registered on purpose: removing this resource would make
# Terraform *unregister* the provider subscription-wide (a shared sub with the
# harness), an unwanted side-effect for a no-longer-used-here but harmless RP.
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
