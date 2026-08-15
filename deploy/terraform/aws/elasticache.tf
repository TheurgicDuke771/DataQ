# ElastiCache Redis — AWS-native managed Redis for the Celery broker + rate
# limits (ADR 0035). Decision: managed ElastiCache, not self-hosted-on-Fargate
# like the Azure stack's Redis Container App — the user's explicit choice for
# the AWS deployment (see the approved plan's cost note: a few dollars/month
# more than self-hosting, ongoing).
#
# aws_elasticache_replication_group (not aws_elasticache_cluster): the
# replication-group resource is what actually supports auth_token +
# transit_encryption_enabled cleanly. One node, no read replica — this is a
# single Celery broker, not a fan-out cache.

resource "aws_elasticache_subnet_group" "app" {
  name       = "dataq-app-redis"
  subnet_ids = aws_subnet.public[*].id
}

resource "aws_security_group" "redis" {
  name        = "dataq-app-redis"
  description = "Redis — inbound only from the ECS tasks security group"
  vpc_id      = aws_vpc.app.id

  ingress {
    description     = "Redis from ECS tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "dataq-app-redis" }
}

# ElastiCache auth_token: 16-128 printable ASCII chars, excluding '/', '"',
# '@'. `special = false` sidesteps the excluded-character set entirely rather
# than trying to allow-list a safe subset.
resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

resource "aws_elasticache_replication_group" "app" {
  replication_group_id = "dataq-app"
  description          = "DataQ Celery broker + rate-limit store"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = "cache.t4g.micro"
  num_cache_clusters   = 1
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.app.name
  security_group_ids = [aws_security_group.redis.id]

  # TLS + password required together — same defense-in-depth posture as the
  # Azure stack's self-hosted Redis (--requirepass over an internal-only
  # ingress). transit_encryption_enabled is what makes auth_token usable at
  # all on this resource.
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth.result

  tags = { Name = "dataq-app-redis" }
}

locals {
  # rediss:// (TLS) scheme — transit_encryption_enabled requires TLS
  # connections; redis-py/celery both understand the scheme.
  redis_url = "rediss://:${random_password.redis_auth.result}@${aws_elasticache_replication_group.app.primary_endpoint_address}:6379/0"
}
