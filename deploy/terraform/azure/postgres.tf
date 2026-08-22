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

# ── Residency assertion for the resource that actually holds the data (G4/#434) ─
#
# This server holds the application's personal data (`results.sample_failures`,
# list-shaped `observed_value`), so it is the resource residency claims are ABOUT.
#
# It is checked against `shared_pg_expected_location` when that is set, and
# against `azure_location` otherwise. For an ordinary deployment — one region,
# everything in it — those are the same thing and the check is silent.
#
# Setting the override is how a deployment records an ACCEPTED EXCEPTION — most
# often because a subscription quota or a shared-platform policy forced the app
# onto a server someone else created, in a region this stack does not choose.
#
# The basis on which such an exception is acceptable is JURISDICTION, not region:
# two regions inside one country engage no GDPR Ch. V transfer, two straddling an
# adequacy boundary do. Record the reasoning wherever this deployment's posture
# lives; concrete regions are deliberately not written into this repo, since they
# differ per deployment and a committed value goes stale silently (#1465).
#
# **Checking against the expected value rather than against `azure_location` is
# what keeps this useful.** The latter would warn on every plan forever, and a
# permanently-firing check is noise people learn to skip — it would mask the
# drift that actually matters, this server moving to another jurisdiction.
#
# A `check` (warns) rather than a `postcondition` (blocks), unlike its sibling on
# the Container Apps environment in aca.tf. The difference is deliberate: a
# blocking assertion here would fail every apply until someone migrated a
# database, and the response to "the DB moved" is a decision, not a rollback.
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
