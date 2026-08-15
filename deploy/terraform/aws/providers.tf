# Provider config. No credentials live here — auth comes from the ambient
# AWS CLI profile (AWS_PROFILE=dataq-deploy, a scoped IAM user; NOT root — see
# README.md's IAM bootstrap section) or AWS_* env vars.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

provider "random" {}
