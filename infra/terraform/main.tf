# =============================================================================
# M&A Due Diligence Copilot — AWS Enterprise Single-Tenant VPC Skeleton
# Provisioning: Customer-Managed KMS Key (CMEK), Isolated VPC, Encrypted S3 VDR
#               Bucket (Versioning + Lifecycle), Multi-AZ RDS PostgreSQL 16,
#               and Private EKS Cluster Reference.
# =============================================================================

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "ma-dd-copilot"
      Environment = var.environment
      Tenant      = var.tenant_name
      Compliance  = "SOC2-ISO27001-GDPR"
      ManagedBy   = "Terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "Primary AWS region (e.g., eu-central-1 for EU GDPR residency)."
  type        = string
  default     = "eu-central-1"
}

variable "environment" {
  description = "Deployment stage (prod, staging, sandbox)."
  type        = string
  default     = "prod"
}

variable "tenant_name" {
  description = "Dedicated single-tenant enterprise client slug."
  type        = string
  default     = "enterprise-ic"
}

variable "vpc_cidr" {
  description = "CIDR block for the isolated single-tenant VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class."
  type        = string
  default     = "db.r6g.large"
}

variable "db_master_password" {
  description = "RDS master password (inject via Secrets Manager / TF_VAR in CI)."
  type        = string
  sensitive   = true
}

variable "eks_cluster_role_arn" {
  description = "IAM Role ARN authorized for EKS control plane."
  type        = string
}

# -----------------------------------------------------------------------------
# 1. Customer-Managed Encryption Key (CMEK / BYOK)
# -----------------------------------------------------------------------------

resource "aws_kms_key" "cmek" {
  description             = "CMEK for ${var.tenant_name} M&A VDR S3, RDS PostgreSQL, and EKS Secrets"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  multi_region            = false
}

resource "aws_kms_alias" "cmek" {
  name          = "alias/madd-${var.tenant_name}-${var.environment}-cmek"
  target_key_id = aws_kms_key.cmek.key_id
}

# -----------------------------------------------------------------------------
# 2. Isolated Single-Tenant VPC & Subnets
# -----------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "tenant_vpc" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "madd-${var.tenant_name}-${var.environment}-vpc"
  }
}

resource "aws_subnet" "private_app" {
  count             = 2
  vpc_id            = aws_vpc.tenant_vpc.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name                              = "madd-${var.tenant_name}-app-${count.index + 1}"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

resource "aws_subnet" "isolated_db" {
  count             = 2
  vpc_id            = aws_vpc.tenant_vpc.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "madd-${var.tenant_name}-db-${count.index + 1}"
  }
}

resource "aws_vpc_endpoint" "s3_gateway" {
  vpc_id            = aws_vpc.tenant_vpc.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
}

# -----------------------------------------------------------------------------
# 3. Encrypted S3 Virtual Data Room (VDR) Bucket with Versioning & Lifecycle
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "vdr_vault" {
  bucket        = "madd-vdr-${var.tenant_name}-${var.environment}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "vdr_vault" {
  bucket                  = aws_s3_bucket.vdr_vault.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "vdr_vault" {
  bucket = aws_s3_bucket.vdr_vault.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "vdr_vault" {
  bucket = aws_s3_bucket.vdr_vault.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.cmek.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "vdr_vault" {
  bucket = aws_s3_bucket.vdr_vault.id

  rule {
    id     = "archive-closed-deal-audit-packs"
    status = "Enabled"
    filter {
      prefix = "audit-packs/"
    }
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    noncurrent_version_expiration {
      noncurrent_days = 2555 # 7-year regulatory retention (GoBD / SEC 17a-4)
    }
  }

  rule {
    id     = "purge-ephemeral-staging"
    status = "Enabled"
    filter {
      prefix = "tmp-uploads/"
    }
    expiration {
      days = 7
    }
  }
}

# -----------------------------------------------------------------------------
# 4. Multi-AZ RDS PostgreSQL 16 (Encrypted with CMEK, Private Only)
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "postgres" {
  name       = "madd-${var.tenant_name}-${var.environment}-db-subnets"
  subnet_ids = aws_subnet.isolated_db[*].id
}

resource "aws_security_group" "rds_sg" {
  name        = "madd-${var.tenant_name}-${var.environment}-rds-sg"
  description = "Restrict PostgreSQL 5432 strictly to private application subnets"
  vpc_id      = aws_vpc.tenant_vpc.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private_app[*].cidr_block
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_db_instance" "postgres" {
  identifier                  = "madd-${var.tenant_name}-${var.environment}-pg"
  engine                      = "postgres"
  engine_version              = "16.3"
  instance_class              = var.db_instance_class
  allocated_storage           = 100
  max_allocated_storage       = 1000
  storage_type                = "gp3"
  storage_encrypted           = true
  kms_key_id                  = aws_kms_key.cmek.arn
  multi_az                    = true
  publicly_accessible         = false
  db_name                     = "madd_copilot"
  username                    = "madd_admin"
  password                    = var.db_master_password
  db_subnet_group_name        = aws_db_subnet_group.postgres.name
  vpc_security_group_ids      = [aws_security_group.rds_sg.id]
  backup_retention_period     = 35
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "madd-${var.tenant_name}-${var.environment}-final"
  auto_minor_version_upgrade  = true
  performance_insights_enabled = true
}

# -----------------------------------------------------------------------------
# 5. Private EKS Cluster Reference (Control-Plane Audit Logging + KMS Secrets)
# -----------------------------------------------------------------------------

resource "aws_eks_cluster" "dd_cluster" {
  name     = "madd-${var.tenant_name}-${var.environment}-eks"
  version  = "1.30"
  role_arn = var.eks_cluster_role_arn

  vpc_config {
    subnet_ids              = aws_subnet.private_app[*].id
    endpoint_private_access = true
    endpoint_public_access  = false
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.cmek.arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "vpc_id" {
  value = aws_vpc.tenant_vpc.id
}

output "cmek_key_arn" {
  value = aws_kms_key.cmek.arn
}

output "vdr_s3_bucket_name" {
  value = aws_s3_bucket.vdr_vault.bucket
}

output "rds_endpoint" {
  value     = aws_db_instance.postgres.endpoint
  sensitive = true
}

output "eks_cluster_name" {
  value = aws_eks_cluster.dd_cluster.name
}
