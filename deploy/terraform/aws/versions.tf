# DataQ APP infra — AWS, OpenTofu / provider pins.

terraform {
  required_version = ">= 1.9"

  backend "local" {
    path = "terraform.tfstate"
  }

  # State encryption (mirrors the Azure stack's #1087 decision — local state holds the generated
  # Postgres/ElastiCache passwords).
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
