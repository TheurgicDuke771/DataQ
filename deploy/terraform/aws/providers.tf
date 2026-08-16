# Provider config. No credentials live here — auth comes from the ambient
# AWS CLI profile (AWS_PROFILE=dataq-deploy, a scoped IAM user; NOT root — see
# README.md's IAM bootstrap section) or AWS_* env vars.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# CloudFront-scoped WAFv2 Web ACLs are a GLOBAL resource and exist only in
# us-east-1, regardless of where the rest of the stack lives (#1387). This alias
# is used by waf.tf and by nothing else — the ACL is the one resource here that
# cannot honour var.aws_region.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.common_tags
  }
}

provider "random" {}
