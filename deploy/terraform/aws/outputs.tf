# Outputs feed the same "wire into GitHub repo secrets/vars" convention as
# the Azure stack's outputs.tf — see README.md's post-apply section.

output "frontend_url" {
  description = "The deployment's public URL (ALB DNS name, HTTP — no custom domain yet)."
  value       = local.frontend_url
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint (host:port)."
  value       = aws_db_instance.app.endpoint
}

output "redis_endpoint" {
  description = "ElastiCache primary endpoint."
  value       = aws_elasticache_replication_group.app.primary_endpoint_address
  sensitive   = true
}

output "cognito_user_pool_id" {
  description = "Cognito user pool id (OIDC_AUDIENCE's issuer path component)."
  value       = aws_cognito_user_pool.app.id
}

output "cognito_client_id" {
  description = "Cognito SPA app client id — OIDC_AUDIENCE / DATAQ_AUTH_CLIENT_ID."
  value       = aws_cognito_user_pool_client.spa.id
}

output "cognito_issuer" {
  description = "OIDC_ISSUER / DATAQ_AUTH_AUTHORITY."
  value       = local.cognito_issuer
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.app.name
}

output "ecs_service_names" {
  value = {
    api      = aws_ecs_service.api.name
    worker   = aws_ecs_service.worker.name
    frontend = aws_ecs_service.frontend.name
  }
}

output "migrate_task_definition_family" {
  value = aws_ecs_task_definition.migrate.family
}

output "github_deploy_role_arn" {
  description = "IAM role ARN the Deploy workflow assumes via OIDC federation — maps to the repo secret used by aws-actions/configure-aws-credentials."
  value       = aws_iam_role.github_deploy.arn
}

output "backend_image_repo" {
  value = var.backend_image_repo
}
