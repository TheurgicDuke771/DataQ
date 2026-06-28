# GitHub Actions -> Azure auth for the Deploy workflow (.github/workflows/deploy.yml)
# via OIDC federated credentials — no stored client secret. The workflow's
# azure/login uses the client/tenant/subscription ids output by this stack; the SP
# gets Contributor on dataq-rg so it can run `az containerapp update` / `job start`
# (the GHCR image push uses the workflow's GITHUB_TOKEN, not this SP).

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

resource "azurerm_role_assignment" "github_deploy_contributor" {
  scope                = data.azurerm_resource_group.dataq.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_deploy.object_id
}
