# DataQ APP infra — Terraform / provider pins.
#
# This stack provisions the *application's own* production resources (ACA api +
# worker + frontend + migrate job + Postgres + Key Vault + App Insights + a
# self-hosted Redis broker) into the
# EXISTING dataq-rg. It is deliberately SEPARATE from the harness stack
# (~/Coding/Python/DataQ-harness/terraform — ADR 0021), which stands up the
# datasources + demo env. Only the subscription + resource group are shared.
#
# Local state backend — state is gitignored, never committed (it contains the
# generated Postgres password + secret values). Pin providers; do not float.

terraform {
  required_version = ">= 1.9"

  backend "local" {
    path = "terraform.tfstate"
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
