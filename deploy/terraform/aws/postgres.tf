# RDS PostgreSQL — the app's own instance (this account is dedicated to this
# deployment, so unlike Azure's shared-server pattern there's no sibling
# harness stack to coordinate with).

resource "aws_db_subnet_group" "app" {
  name       = "dataq-app-db"
  subnet_ids = aws_subnet.public[*].id
}

resource "aws_security_group" "rds" {
  name        = "dataq-app-rds"
  description = "Postgres - inbound only from the ECS tasks security group"
  vpc_id      = aws_vpc.app.id

  ingress {
    description     = "Postgres from ECS tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "dataq-app-rds" }
}

resource "aws_db_instance" "app" {
  identifier     = "dataq-app"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.app_db_name
  username = var.app_db_user
  password = var.app_db_password

  db_subnet_group_name   = aws_db_subnet_group.app.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  # Not in a NAT'd private subnet (see main.tf's decision note) — kept
  # unreachable from the internet by NOT setting a public IP, independent of
  # the subnet's own IGW route.
  publicly_accessible = false

  # Bring-up posture, mirrors the Azure stack's key_vault_purge_protection
  # default — flip these once this becomes a durable deployment.
  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 1

  tags = { Name = "dataq-app-db" }
}

locals {
  # sslmode=require: RDS enforces TLS by default. urlencode() avoids a
  # DSN-breaking special character in the password, same as the Azure stack.
  database_url = "postgresql+psycopg2://${var.app_db_user}:${urlencode(var.app_db_password)}@${aws_db_instance.app.address}:5432/${var.app_db_name}?sslmode=require"
}
