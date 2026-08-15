# Networking: one VPC, 2 public subnets across 2 AZs, an Internet Gateway, no
# NAT Gateway (decision: public-subnets-no-NAT — saves ~$33/mo; ECS tasks get
# no public IP/inbound exposure since security groups gate everything, only
# the ALB->frontend path is internet-reachable). RDS and ElastiCache also sit
# in these public subnets with no public IP/endpoint access, for the same
# reason the Azure stack doesn't stand up a separate private-subnet tier for
# a single-environment deployment. Documented MVP tradeoff — see the approved
# deployment plan; harden to private subnets + NAT/VPC endpoints later if a
# real compliance/traffic need justifies the cost.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  # All DataQ-APP-AWS resources carry this tag set so they're trivially
  # distinguishable from anything else that might land in this account later.
  common_tags = {
    Project = "dataq"
    Managed = "terraform"
    Purpose = "dataq-app-aws"
  }

  az_count     = 2
  azs          = slice(data.aws_availability_zones.available.names, 0, local.az_count)
  vpc_cidr     = "10.20.0.0/16"
  public_cidrs = [for i in range(local.az_count) : cidrsubnet(local.vpc_cidr, 4, i)]
}

resource "aws_vpc" "app" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "dataq-app-vpc" }
}

resource "aws_internet_gateway" "app" {
  vpc_id = aws_vpc.app.id

  tags = { Name = "dataq-app-igw" }
}

resource "aws_subnet" "public" {
  count                   = local.az_count
  vpc_id                  = aws_vpc.app.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "dataq-app-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.app.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.app.id
  }

  tags = { Name = "dataq-app-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = local.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

data "aws_caller_identity" "current" {}
