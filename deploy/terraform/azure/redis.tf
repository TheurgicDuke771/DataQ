# Self-hosted Redis broker for Celery (decision: self-hosted Container App, not Azure Cache for
# Redis — cheapest for a transient broker; no persistence needed).

# Defense-in-depth: even though ingress is internal-only, the broker still requires a
# password (--requirepass) so a compromised neighbour can't use it unauthenticated.
resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "azurerm_container_app" "redis" {
  name                         = "dataq-app-redis"
  container_app_environment_id = data.azurerm_container_app_environment.shared.id
  resource_group_name          = data.azurerm_resource_group.dataq.name
  revision_mode                = "Single"

  secret {
    name  = "redis-password"
    value = random_password.redis.result
  }

  ingress {
    external_enabled = false
    transport        = "tcp"
    target_port      = 6379
    exposed_port     = 6379
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name = "redis"
      # 7.x is a deliberate SECURITY pin, not staleness: Redis 8 bundles the Vector Sets
      # module, whose duplicate-HNSW-ID RESTORE path is an unpatched RCE. Read #980 before
      # bumping to 8.x — Dependabot does NOT watch Docker images, nothing else will raise
      # it (dependabot.yml's `redis` ignore rule caps redis-py, not this server image).
      image   = "redis:7-alpine" # public image — no registry auth
      cpu     = 0.5
      memory  = "1Gi"
      command = ["sh", "-c", "exec redis-server --requirepass \"$REDIS_PASSWORD\""]
      env {
        name        = "REDIS_PASSWORD"
        secret_name = "redis-password"
      }
    }
  }

  tags = local.common_tags
}

locals {
  # Password-authenticated broker URL — carries the secret, so it's injected as a
  # Container App secret (not a plain env) on the api + worker.
  redis_url = "redis://:${random_password.redis.result}@${azurerm_container_app.redis.name}:6379/0"
}
