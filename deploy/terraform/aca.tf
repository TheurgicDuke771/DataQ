# The Container Apps environment is SHARED with the harness: this subscription is
# capped at ONE Container App Environment, so we do NOT create one here — the
# harness Terraform owns it (renamed to the neutral `dataq-cae`). The app stack
# only REFERENCES it; the app's own apps/redis/migrate job run inside it but stay
# separate, dataq-app-* resources. The env lives in azure_location (westus2), so
# the app's container resources land there too.
data "azurerm_container_app_environment" "shared" {
  name                = "dataq-cae"
  resource_group_name = data.azurerm_resource_group.dataq.name
}
