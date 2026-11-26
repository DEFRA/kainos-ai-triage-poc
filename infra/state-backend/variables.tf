variable "allowed_state_principal_arns" {
  description = "IAM principals allowed to access the project state and lock objects."
  type        = set(string)

  validation {
    condition     = length(var.allowed_state_principal_arns) > 0
    error_message = "At least one approved state-access principal ARN is required."
  }

}

variable "allowed_deployer_principal_arns" {
  description = "Approved IAM principals that may assume the application-state deployer role."
  type        = set(string)

  validation {
    condition     = length(var.allowed_deployer_principal_arns) > 0
    error_message = "At least one approved deployer trust principal ARN is required."
  }
}
