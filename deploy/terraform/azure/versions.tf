# DataQ APP infra — OpenTofu / provider pins.

terraform {
  # OpenTofu version constraint.
  required_version = ">= 1.9"

  backend "local" {
    path = "terraform.tfstate"
  }

  # State encryption (#1087).
  encryption {
    key_provider "pbkdf2" "local" {
      passphrase = var.state_encryption_passphrase
    }
    method "aes_gcm" "primary" {
      keys = key_provider.pbkdf2.local
    }
    state {
      method = method.aes_gcm.primary
    }
    # Plan files embed resolved sensitive values, so they get the same treatment.
    plan {
      method = method.aes_gcm.primary
    }
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
