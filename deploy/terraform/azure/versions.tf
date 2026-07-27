# DataQ APP infra — OpenTofu / provider pins.
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
#
# The CLI is **OpenTofu** (`tofu`), not Terraform — ADR 0024 amendment
# (2026-07-27). Providers resolve from registry.opentofu.org; `.terraform.lock.hcl`
# records those hashes. The `terraform {}` block name and the `.tf` extension are
# OpenTofu's own file format, not a leftover — do not rename them.

terraform {
  # OpenTofu version constraint. The numeric floor is unchanged from the
  # Terraform era, but it now constrains OpenTofu's version line — validated on
  # OpenTofu 1.12.5. Note this config is still *parseable* by Terraform: nothing
  # here is OpenTofu-exclusive yet, which is what keeps the migration reversible.
  # Adding the `encryption {}` block (follow-up) is what makes it structural.
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
