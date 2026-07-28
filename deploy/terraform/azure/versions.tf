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

  # State encryption (#1087). The local state holds the generated Postgres password,
  # the Redis password and webhook secret values; unencrypted it is a plaintext
  # credential file on one laptop.
  #
  # This is ALSO the point at which the config stops being Terraform-parseable —
  # Terraform cannot read an `encryption` block — so it is what makes the ADR 0024
  # OpenTofu amendment structural rather than conventional.
  #
  # What this does and does not buy: the passphrase lives beside the ciphertext, so
  # against host compromise it buys little. It buys a lot against ACCIDENTAL
  # disclosure — a committed state file, a `.tfstate.backup` swept into a backup, a
  # state pasted into a ticket — which turns a full credential leak into an opaque blob.
  #
  # The `unencrypted` fallback used for the one-time migration has been removed; state
  # is now encrypted-only and a wrong/missing passphrase fails closed
  # ("cipher: message authentication failed") without corrupting the file.
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
