resource "aws_db_subnet_group" "frontend" {
  count = var.enable_frontend_rds ? 1 : 0

  name       = "ai-triage-rds-db-frontend"
  subnet_ids = var.alb_subnet_ids

  tags = {
    Name = "ai-triage-rds-db-frontend"
  }
}

resource "aws_db_instance" "frontend" {
  count = var.enable_frontend_rds ? 1 : 0

  # The console template is not a Terraform argument; its settings are explicit below.
  identifier                  = "ai-triage-rds-db-frontend"
  engine                      = "postgres"
  engine_version              = "18.6"
  instance_class              = var.frontend_rds_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  multi_az                    = false
  publicly_accessible         = false
  db_subnet_group_name        = aws_db_subnet_group.frontend[0].name
  vpc_security_group_ids      = [aws_security_group.frontend_rds[0].id]
  db_name                     = "aitriage"
  username                    = "dbadmin"
  manage_master_user_password = true
  backup_retention_period     = 7
  copy_tags_to_snapshot       = true
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "ai-triage-rds-db-frontend-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  lifecycle {
    prevent_destroy = true

    ignore_changes = [
      final_snapshot_identifier
    ]
  }

  tags = {
    Name = "ai-triage-rds-db-frontend"
  }
}

# Next rotation falls after the mid-December 2026 decommission; do not rotate on apply.
resource "aws_secretsmanager_secret_rotation" "frontend_rds_master" {
  count = var.enable_frontend_rds ? 1 : 0

  secret_id          = aws_db_instance.frontend[0].master_user_secret[0].secret_arn
  rotate_immediately = false

  rotation_rules {
    automatically_after_days = 30
  }
}
