# Provider config. No credentials live here — auth comes from the ambient `az
# login` session (Owner on the subscription) or ARM_*/AZURE_* env vars. This
# stack applies as the Owner user (not the harness's Contributor-only SP), so it
# CAN register resource providers (rp.tf) and create role assignments
# (identity.tf / github_oidc.tf).

provider "azurerm" {
  features {
    key_vault {
      # Purge soft-deleted vaults on destroy so a re-apply can reuse the name.
      purge_soft_delete_on_destroy = true
    }
  }
  # We register the 4 missing RPs explicitly in rp.tf; don't let the provider
  # mass-register every RP on the subscription.
  resource_provider_registrations = "none"
}

provider "azuread" {
  # Tenant comes from the az login session (Default Directory). Creating the
  # GitHub-deploy app registration needs app-registration rights — fine as the
  # directory owner.
}

provider "random" {}
provider "time" {}
provider "null" {}
