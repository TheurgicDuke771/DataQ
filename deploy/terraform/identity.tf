# User-assigned managed identity for the api + worker container apps. The app's
# SecretStore (SECRET_STORE=azure_key_vault) authenticates to Key Vault via
# DefaultAzureCredential, which resolves THIS identity at runtime to read the
# datasource connection secrets. The Key Vault Secrets User role assignment lives
# in keyvault.tf (it needs the vault id).

resource "azurerm_user_assigned_identity" "app" {
  name                = "dataq-app-id"
  location            = var.azure_location
  resource_group_name = data.azurerm_resource_group.dataq.name
  tags                = local.common_tags
}
