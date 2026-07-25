# App database — a DISTINCT `dataq` database on the SHARED Postgres Flexible Server
# (this subscription caps Flexible Servers at 1, so the app shares the harness's
# server — renamed neutrally to dataq-pg-* / purpose=dataq-shared). The app connects
# as the least-privilege `dataq_app` role, which OWNS only the `dataq` database (no
# access to the `airflow` metadata DB on the same server).
#
# The `dataq_app` role + `dataq` database are provisioned out-of-band (a one-off
# psql against the server — see deploy/terraform/README.md "Shared Postgres"),
# keeping this stack connection-free (no postgres provider / no plan-time DB
# connection, so it stays CI-friendly). Its password is passed in as the sensitive
# var.app_db_password and injected as the DATABASE_URL Container App secret.
#
# Runtime reachability: the ACA apps connect over the server's allow-Azure-services
# firewall rule (the apps are Azure services), same as airflow.
#
# SECURITY CONSTRAINT — the single-role model is load-bearing, not just tidy.
# PostgreSQL's referential-integrity check switches to the REFERENCED TABLE'S OWNER
# before invoking an implicit cast on the FK value (ri_triggers.c). A role that can
# CREATE TYPE / FUNCTION / CAST and holds REFERENCES on another owner's table can
# therefore run its own SQL as that owner — reaching COPY ... TO PROGRAM if the owner
# has pg_execute_server_program. Unpatched upstream as of 2026-07-24.
# We are not exposed because there is exactly ONE app role (`dataq_app`) and it is
# not shared with any untrusted party. Adding a second, less-trusted role to the
# `dataq` database is what would create the exposure — so do not, without revisiting
# this. See the guardrail row in docs/progress.md.

data "azurerm_postgresql_flexible_server" "shared" {
  name                = var.shared_pg_server_name
  resource_group_name = data.azurerm_resource_group.dataq.name
}

locals {
  # psycopg2 URL the backend Settings read as DATABASE_URL. sslmode=require —
  # Flexible Server enforces TLS. The password is URL-encoded so a future value
  # containing URL-significant chars (@ : / ? #) can't break DSN parsing (#395);
  # app_db_user/name are controlled alnum identifiers, no encoding needed.
  database_url = join("", [
    "postgresql+psycopg2://",
    var.app_db_user, ":", urlencode(var.app_db_password),
    "@", data.azurerm_postgresql_flexible_server.shared.fqdn, ":5432/",
    var.app_db_name, "?sslmode=require",
  ])
}
