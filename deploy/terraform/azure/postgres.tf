# App database — a `dataq` database the app connects to as its one role, `dataq_app`. By default
# the stack creates a server for it. With create_shared_resources = false it attaches to an
# existing server instead (a subscription capped at one Flexible Server may share it with other
# workloads), where the database and role are provisioned out-of-band (README.md).
#
# SECURITY CONSTRAINT — the single-role model is load-bearing, not just tidy. Postgres's
# RI check runs implicit casts as the REFERENCED table's OWNER (ri_triggers.c), so a role
# with CREATE TYPE/CAST + REFERENCES on another owner's table can run SQL as that owner.
# Unpatched upstream as of 2026-07-24. We are not exposed ONLY because `dataq_app` is the
# single app role — adding a second, less-trusted role to the `dataq` database is what
# would create the exposure; do not, without revisiting this (docs/progress.md guardrail).

# Created (the default): a server of the app's own, whose administrator is the app's one role,
# as on AWS RDS. The single-role constraint above holds: `dataq` has no second role.
resource "azurerm_postgresql_flexible_server" "shared" {
  count                         = var.create_shared_resources ? 1 : 0
  name                          = coalesce(var.shared_pg_server_name, "dataq-pg-${random_string.suffix.result}")
  resource_group_name           = local.resource_group_name
  location                      = var.azure_location
  version                       = "16"
  sku_name                      = var.pg_sku_name
  storage_mb                    = var.pg_storage_mb
  administrator_login           = var.app_db_user
  administrator_password        = var.app_db_password
  public_network_access_enabled = true
  backup_retention_days         = 7
  tags                          = local.common_tags

  lifecycle {
    # Azure picks the zone when none is given, and reports it back.
    ignore_changes = [zone]
  }
}

# The app reaches the server over the allow-Azure-services rule, as on a referenced server.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  count            = var.create_shared_resources ? 1 : 0
  name             = "AllowAllAzureServicesAndResourcesWithinAzureIps"
  server_id        = azurerm_postgresql_flexible_server.shared[0].id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  count     = var.create_shared_resources ? 1 : 0
  name      = var.app_db_name
  server_id = azurerm_postgresql_flexible_server.shared[0].id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Referenced (create_shared_resources = false): the database and role are provisioned
# out-of-band (README.md).
data "azurerm_postgresql_flexible_server" "shared" {
  count               = var.create_shared_resources ? 0 : 1
  name                = var.shared_pg_server_name
  resource_group_name = local.resource_group_name

  lifecycle {
    precondition {
      condition     = var.shared_pg_server_name != null
      error_message = "shared_pg_server_name is required when create_shared_resources = false: name the existing server."
    }
  }
}

locals {
  pg_fqdn = one(concat(
    azurerm_postgresql_flexible_server.shared[*].fqdn,
    data.azurerm_postgresql_flexible_server.shared[*].fqdn,
  ))
  pg_location = one(concat(
    azurerm_postgresql_flexible_server.shared[*].location,
    data.azurerm_postgresql_flexible_server.shared[*].location,
  ))
}

# ── Residency assertion for the resource that actually holds the data (G4/#434) ─ This server holds
# the application's personal data (`results.sample_failures`, list-shaped `observed_value`).
check "database_residency" {
  assert {
    condition     = lower(replace(local.pg_location, " ", "")) == lower(replace(coalesce(var.shared_pg_expected_location, var.azure_location), " ", ""))
    error_message = <<-EOT
      Residency drift (G4/#434, #1465): the shared PostgreSQL server is in
      '${local.pg_location}' but
      the expected region is '${coalesce(var.shared_pg_expected_location, var.azure_location)}'.
      This server holds the application's personal data, so a move is a residency
      change even when it looks routine. Confirm the new region is in the intended
      JURISDICTION, then update the variable and the matrix in docs/site/security.md.
    EOT
  }
}

locals {
  # psycopg2 URL the backend Settings read as DATABASE_URL. sslmode=require — Flexible Server
  # enforces TLS.
  database_url = join("", [
    "postgresql+psycopg2://",
    var.app_db_user, ":", urlencode(var.app_db_password),
    "@", local.pg_fqdn, ":5432/",
    var.app_db_name, "?sslmode=require",
  ])
}
