# GitHub Actions -> Azure auth for the Deploy workflow (.github/workflows/deploy.yml) via OIDC
# federated credentials — no stored client secret.

resource "azuread_application" "github_deploy" {
  display_name = "dataq-github-deploy"
}

resource "azuread_service_principal" "github_deploy" {
  client_id = azuread_application.github_deploy.client_id
}

resource "azuread_application_federated_identity_credential" "github_deploy" {
  application_id = azuread_application.github_deploy.id
  display_name   = "github-actions-${var.github_environment}"
  description    = "GitHub Actions OIDC for the ${var.github_environment} environment deploy"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  # Must match the workflow's `environment: production` job scoping.
  subject = "repo:${var.github_repo}:environment:${var.github_environment}"
}

# Least privilege: scope Contributor to ONLY the four deploy targets, not the whole RG — dataq-rg is
# SHARED with the harness.
locals {
  github_deploy_targets = {
    api      = azurerm_container_app.api.id
    worker   = azurerm_container_app.worker.id
    migrate  = azurerm_container_app_job.migrate.id
    frontend = azurerm_container_app.frontend.id
  }
}

resource "azurerm_role_assignment" "github_deploy_contributor" {
  for_each             = local.github_deploy_targets
  scope                = each.value
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_deploy.object_id
}

# `az containerapp update --image` / `job start` resolve the app's managed environment (the harness-
# owned shared dataq-cae); without read there the deploy can 403.
resource "azurerm_role_assignment" "github_deploy_env_reader" {
  scope                = data.azurerm_container_app_environment.shared.id
  role_definition_name = "Reader"
  principal_id         = azuread_service_principal.github_deploy.object_id
}
