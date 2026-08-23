# User-assigned managed identity for the api + worker container apps.

resource "azurerm_user_assigned_identity" "app" {
  name                = "dataq-app-id"
  location            = var.azure_location
  resource_group_name = data.azurerm_resource_group.dataq.name
  tags                = local.common_tags
}
