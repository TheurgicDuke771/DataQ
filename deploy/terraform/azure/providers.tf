# Provider config.

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
  # Tenant comes from the az login session (Default Directory).
}

provider "random" {}
provider "time" {}
provider "null" {}
