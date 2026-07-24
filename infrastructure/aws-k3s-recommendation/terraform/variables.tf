variable "aws_region" {
  description = "The AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "The environment (e.g., prod, dev)"
  type        = string
  default     = "prod"
}

variable "project_name" {
  description = "The name of the project"
  type        = string
  default     = "grid-k3s"
}

variable "control_plane_instance_type" {
  description = "EC2 instance type for the k3s control plane"
  type        = string
  default     = "t3a.large" # 2 vCPU, 8GB RAM - Good starting point for K8s API + Etcd + System apps
}

variable "worker_instance_type" {
  description = "EC2 instance type for the k3s worker nodes"
  type        = string
  default     = "t3a.xlarge" # 4 vCPU, 16GB RAM - For hosting multiple user apps
}

variable "worker_count" {
  description = "Number of initial AWS worker nodes (you can add VPS nodes later manually)"
  type        = number
  default     = 2
}

variable "k3s_token" {
  description = "Shared secret token for nodes to join the k3s cluster. Generate securely."
  type        = string
  sensitive   = true
}

variable "tailscale_auth_key" {
  description = "Optional: Tailscale auth key to automatically join nodes to the mesh VPN for multi-VPS networking."
  type        = string
  sensitive   = true
  default     = ""
}

variable "ssh_key_name" {
  description = "Name of an existing AWS Key Pair to allow SSH access to the instances."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}
