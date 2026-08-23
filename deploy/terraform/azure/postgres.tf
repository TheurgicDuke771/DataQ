# App database — a DISTINCT `dataq` database on the SHARED Postgres Flexible Server (this
# subscription caps Flexible Servers at 1, so the app shares the harness's server — renamed
# neutrally to dataq-pg-* / purpose=dataq-shared). The app connects as the least-privilege
# `dataq_app` role (owns only `dataq`; provisioned out-of-band — see deploy/terraform/README.md).
#
# SECURITY CONSTRAINT — the single-role model is load-bearing, not just tidy. Postgres's
# RI check runs implicit casts as the REFERENCED table's OWNER (ri_triggers.c), so a role
# with CREATE TYPE/CAST + REFERENCES on another owner's table can run SQL as that owner.
# Unpatched upstream as of 2026-07-24. We are not exposed ONLY because `dataq_app` is the
# single app role — adding a second, less-trusted role to the `dataq` database is what
# would create the exposure; do not, without revisiting this (docs/progress.md guardrail).

data "azurerm_postgresql_flexible_server" "shared" {
  name                = var.shared_pg_server_name
  resource_group_name = data.azurerm_resource_group.dataq.name
}

# ── Residency assertion for the resource that actually holds the data (G4/#434) ─ This server holds
# the application's personal data (`results.sample_failures`, list-shaped `observed_value`).
check "database_residency" {
  assert {
    condition     = lower(replace(data.azurerm_postgresql_flexible_server.shared.location, " ", "")) == lower(replace(coalesce(var.shared_pg_expected_location, var.azure_location), " ", ""))
    error_message = <<-EOT
      Residency drift (G4/#434, #1465): the shared PostgreSQL server is in
      '${data.azurerm_postgresql_flexible_server.shared.location}' but
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
    "@", data.azurerm_postgresql_flexible_server.shared.fqdn, ":5432/",
    var.app_db_name, "?sslmode=require",
  ])
}
