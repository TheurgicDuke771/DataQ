# Provider config.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# CloudFront-scoped WAFv2 Web ACLs are a GLOBAL resource and exist only in us-east-1, regardless of
# where the rest of the stack lives (#1388).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.common_tags
  }
}

provider "random" {}
