# Both ways the stack meets its resource group, Container Apps environment and Postgres server,
# planned against mocked providers (no Azure credentials). The two secret inputs come from the
# environment, never this file: any values do, e.g.
#   TF_VAR_app_db_password=$(openssl rand -hex 16) \
#   TF_VAR_state_encryption_passphrase=$(openssl rand -hex 16) tofu test
# A plan against a real subscription is part of the cloud live-verification epic.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000003"
      client_id       = "00000000-0000-0000-0000-000000000004"
    }
  }
  mock_data "azurerm_subscription" {
    defaults = {
      id              = "/subscriptions/00000000-0000-0000-0000-000000000003"
      subscription_id = "00000000-0000-0000-0000-000000000003"
      tenant_id       = "00000000-0000-0000-0000-000000000001"
    }
  }
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg"
      location = "westus2"
    }
  }
  mock_data "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.App/managedEnvironments/dataq-cae"
      location       = "westus2"
      default_domain = "existing.containerapps.example"
    }
  }
  mock_data "azurerm_postgresql_flexible_server" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/existing-pg"
      location = "westus2"
      fqdn     = "existing-pg.postgres.example"
    }
  }
  # Computed IDs other resources validate: Azure-shaped, not the mock's random strings.
  mock_resource "azurerm_key_vault" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.KeyVault/vaults/dataq-kv" }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dataq-app"
      principal_id = "00000000-0000-0000-0000-000000000009"
      client_id    = "00000000-0000-0000-0000-00000000000a"
    }
  }
  mock_resource "azurerm_container_app" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.App/containerApps/dataq-app" }
  }
  mock_resource "azurerm_container_app_job" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.App/jobs/dataq-app-migrate" }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.OperationalInsights/workspaces/dataq-app-logs" }
  }
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/dataq-pg"
      fqdn = "dataq-pg.postgres.example"
    }
  }
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/dataq-rg/providers/Microsoft.App/managedEnvironments/dataq-cae"
      default_domain = "created.containerapps.example"
    }
  }
}

mock_provider "azuread" {
  mock_data "azuread_client_config" {
    defaults = {
      object_id = "00000000-0000-0000-0000-000000000002"
      tenant_id = "00000000-0000-0000-0000-000000000001"
      client_id = "00000000-0000-0000-0000-000000000004"
    }
  }
  mock_resource "azuread_application" {
    defaults = {
      id        = "/applications/00000000-0000-0000-0000-000000000005"
      object_id = "00000000-0000-0000-0000-000000000005"
      client_id = "00000000-0000-0000-0000-000000000006"
    }
  }
  mock_resource "azuread_service_principal" {
    defaults = { object_id = "00000000-0000-0000-0000-000000000007" }
  }
}

mock_provider "random" {
  mock_resource "random_uuid" {
    defaults = { result = "00000000-0000-0000-0000-000000000008" }
  }
}
mock_provider "time" {}
mock_provider "null" {}

variables {
  image_tag                   = "v1.3.0"
  frontend_image_tag          = "v1.3.0"
  azure_location              = "westus2"
}

run "an_empty_subscription_gets_all_three_created" {
  command = plan

  assert {
    condition = (
      length(azurerm_resource_group.dataq) == 1
      && length(azurerm_container_app_environment.shared) == 1
      && length(azurerm_postgresql_flexible_server.shared) == 1
      && length(azurerm_postgresql_flexible_server_database.app) == 1
      && length(azurerm_postgresql_flexible_server_firewall_rule.azure_services) == 1
    )
    error_message = "with create_shared_resources = true the stack must create the RG, the environment, the server, its database and its firewall rule"
  }
  assert {
    condition = (
      length(data.azurerm_resource_group.dataq) == 0
      && length(data.azurerm_container_app_environment.shared) == 0
      && length(data.azurerm_postgresql_flexible_server.shared) == 0
    )
    error_message = "an empty subscription must not need any of them to exist already"
  }
  assert {
    condition     = azurerm_postgresql_flexible_server.shared[0].administrator_login == "dataq_app"
    error_message = "the created server's administrator is the app's one role"
  }
  assert {
    condition     = azurerm_container_app_environment.shared[0].name == "dataq-cae"
    error_message = "the environment keeps its default name"
  }
}

run "the_environment_name_is_a_variable" {
  command = plan

  variables {
    container_app_environment_name = "acme-cae"
  }

  assert {
    condition     = azurerm_container_app_environment.shared[0].name == "acme-cae"
    error_message = "container_app_environment_name must name the environment"
  }
}

run "existing_resources_are_referenced_not_created" {
  command = plan

  variables {
    create_shared_resources = false
    shared_pg_server_name   = "existing-pg"
  }

  assert {
    condition = (
      length(azurerm_resource_group.dataq) == 0
      && length(azurerm_container_app_environment.shared) == 0
      && length(azurerm_postgresql_flexible_server.shared) == 0
      && length(azurerm_postgresql_flexible_server_database.app) == 0
    )
    error_message = "with create_shared_resources = false nothing shared may be created"
  }
  assert {
    condition     = local.pg_fqdn == "existing-pg.postgres.example"
    error_message = "the app must connect to the existing server"
  }
  assert {
    condition     = local.container_app_environment_default_domain == "existing.containerapps.example"
    error_message = "the apps' FQDNs derive from the existing environment"
  }
}

run "an_existing_server_must_be_named" {
  command = plan

  variables {
    create_shared_resources = false
  }

  expect_failures = [data.azurerm_postgresql_flexible_server.shared]
}
