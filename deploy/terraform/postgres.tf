# App database — Postgres Flexible Server (Burstable B1ms). Public access +
# "allow Azure services" so the Container Apps environment reaches it without VNet
# integration (single-tenant v1). Password generated locally — never committed;
# state is gitignored. westus3 (see postgres_location) avoids the wus2 offer
# restriction the harness also hit.

resource "random_password" "pg" {
  length  = 28
  special = false # keep the SQLAlchemy URL clean (no %-encoding)
}

resource "azurerm_postgresql_flexible_server" "app" {
  name                          = "dataq-app-pg-wus3-${random_string.suffix.result}"
  resource_group_name           = data.azurerm_resource_group.dataq.name
  location                      = var.postgres_location
  version                       = "16"
  administrator_login           = var.postgres_admin_login
  administrator_password        = random_password.pg.result
  sku_name                      = var.postgres_sku
  storage_mb                    = 32768
  auto_grow_enabled             = true
  public_network_access_enabled = true
  zone                          = "1"
  tags                          = local.common_tags
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = "dataq"
  server_id = azurerm_postgresql_flexible_server.app.id
  collation = "en_US.utf8"
  charset   = "utf8"
}

# start=end=0.0.0.0 is Azure's special "allow access from Azure services" rule.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.app.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

locals {
  # psycopg2 URL the backend Settings read as DATABASE_URL. sslmode=require —
  # Flexible Server enforces TLS.
  database_url = join("", [
    "postgresql+psycopg2://",
    var.postgres_admin_login, ":", random_password.pg.result,
    "@", azurerm_postgresql_flexible_server.app.fqdn, ":5432/",
    azurerm_postgresql_flexible_server_database.app.name, "?sslmode=require",
  ])
}
