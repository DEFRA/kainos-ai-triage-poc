variable "owner" {
  description = "Approved owner value for AWS resource tags; null until agreed."
  type        = string
  default     = null
  nullable    = true
}

variable "cost_centre" {
  description = "Approved cost centre value for AWS resource tags; null until agreed."
  type        = string
  default     = null
  nullable    = true
}

variable "vpc_id" {
  description = "ID of the existing VPC provided for the PoC."
  type        = string
}

variable "alb_subnet_ids" {
  description = "IDs of existing subnets for the internal ALB, in at least two Availability Zones."
  type        = set(string)

  validation {
    condition     = length(var.alb_subnet_ids) >= 2
    error_message = "Provide at least two existing subnet IDs for the ALB."
  }
}

variable "app_hostname" {
  description = "Internal hostname served by CCoE for the PoC; the certificate replica covers *.aws-int.defra.cloud."
  type        = string
  default     = "ne-triage-poc.aws-int.defra.cloud"
}

variable "nlb_cidr" {
  description = "Defra internal range (laptops, OpenVPN via ops) allowed in and out of the NLB on TCP 443, per CCoE."
  type        = string
  default     = "10.212.0.0/16"
}

variable "rds_admin_cidrs" {
  description = "Defra laptop ranges allowed to reach the frontend RDS on 5432, per CCoE."
  type        = set(string)
  default     = ["10.144.0.0/16", "10.255.0.0/16"]
}

variable "alb_certificate_arn" {
  description = "ACM certificate ARN supplied by Amrik for the internal ALB HTTPS listener."
  type        = string
  default     = null
  nullable    = true
}

variable "ecs_subnet_id" {
  description = "Existing subnet for the single-AZ replacement UI task."
  type        = string

  validation {
    condition     = contains(var.alb_subnet_ids, var.ecs_subnet_id)
    error_message = "The ECS subnet must be one of the selected ALB subnets."
  }
}

variable "ui_image_repository" {
  description = "ECR repository holding the UI image."
  type        = string
  default     = "ai-triage-container-repository"
}

variable "ui_image_tag" {
  description = "ECR tag resolved to a digest at plan time; the task definition is still pinned by digest."
  type        = string
  default     = "latest"
}

variable "alert_emails" {
  description = "Email addresses for service alarms and new-image notifications; set in terraform.tfvars, not committed."
  type        = set(string)
  default     = []
}

variable "ui_log_retention_days" {
  description = "Approved CloudWatch Logs retention in days for the UI service."
  type        = number
  default     = 60

  validation {
    condition = contains([
      1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731,
      1096, 1827, 2192, 2557, 2922, 3288, 3653
    ], var.ui_log_retention_days)
    error_message = "Choose a CloudWatch Logs retention period supported by AWS."
  }
}

variable "enable_legacy_bucket_versioning" {
  description = "Enable versioning only after bucket adoption and an agreed write-pause window."
  type        = bool
  default     = false
}

variable "enforce_legacy_bucket_https" {
  description = "Add HTTPS-only bucket policy only after adoption and client and policy checks."
  type        = bool
  default     = false
}

variable "enable_frontend_rds" {
  description = "Opt in to the frontend database after reviewing its plan and ongoing costs."
  type        = bool
  default     = false
}

variable "frontend_rds_instance_class" {
  description = "RDS instance class for the frontend database; confirm sizing with Josh."
  type        = string
  default     = "db.t4g.micro"
}
