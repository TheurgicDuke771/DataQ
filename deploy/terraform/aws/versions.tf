# DataQ APP infra — AWS, OpenTofu / provider pins.
#
# This stack provisions a SECOND, parallel deployment of DataQ's application
# resources (ECS Fargate api + worker + frontend + migrate task + RDS +
# ElastiCache + Cognito) into a fresh, dedicated AWS account. Unlike the Azure
# stack (deploy/terraform/azure/), nothing here is shared with another stack —
# the account exists only for this deployment, so there is no "ensure it
# exists, never destroy it" pattern for a resource group equivalent.
#
# Local state backend — state is gitignored, never committed (it contains the
# generated Postgres password + other secret values). Pin providers; do not
# float.
#
# The CLI is OpenTofu (`tofu`), not Terraform — ADR 0024 amendment
# (2026-07-27), same rationale as the Azure stack: Terraform has been
# BUSL-1.1 since v1.6, the last source-available binary touching an
# MIT-distributed project. Providers resolve from registry.opentofu.org.

terraform {
  required_version = ">= 1.9"

  backend "local" {
    path = "terraform.tfstate"
  }

  # State encryption (mirrors the Azure stack's #1087 decision — local state
  # holds the generated Postgres/ElastiCache passwords). A wrong/missing
  # passphrase fails closed ("cipher: message authentication failed") without
  # corrupting the file; losing the passphrase makes the state permanently
  # unreadable, so keep a second copy off this machine (see README.md).
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
    plan {
      method = method.aes_gcm.primary
    }
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
