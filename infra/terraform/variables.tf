###############################################################################
# Sacramento County WCAG PDF Remediation Pipeline
# Terraform Variables
#
# Usage:
#   terraform apply -var="project_id=your-gcp-project" \
#                   -var="environment=dev"
#
# All sensitive values (API keys, service account paths) are consumed
# from environment variables at runtime — never stored here.
###############################################################################

variable "project_id" {
  description = "GCP project ID where all resources will be created. Must already exist and have billing enabled."
  type        = string
  # No default — must be supplied explicitly to prevent accidental deployments
  # to the wrong project.
}

variable "region" {
  description = "GCP region for Cloud Run, Pub/Sub, and GCS resources. us-central1 is used for lowest latency to Sacramento County networks."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment. Controls resource naming prefixes and retention policies."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "container_image_tag" {
  description = "Docker image tag to deploy across all Cloud Run services. Typically a git commit SHA or semver tag. Defaults to 'latest' for local dev — always pin explicitly in staging/prod."
  type        = string
  default     = "latest"
}

variable "gcs_location" {
  description = "Multi-regional or regional GCS bucket location. US multi-region provides redundancy; us-central1 reduces egress costs when Cloud Run is in the same region."
  type        = string
  default     = "US"
}

variable "cloud_run_concurrency" {
  description = "Maximum concurrent requests per Cloud Run container instance. Each PDF page processing consumes ~128MB RAM; 80 concurrent requests balances throughput vs. memory."
  type        = number
  default     = 80
}

variable "cloud_run_min_instances" {
  description = "Minimum number of Cloud Run instances to keep warm. 0 allows scale-to-zero (cheaper); 1 eliminates cold starts for interactive workloads like the HITL dashboard API."
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Maximum Cloud Run instances for autoscaling. 10 handles ~100 concurrent documents (per NFR) with headroom. Increase for production after load testing."
  type        = number
  default     = 10
}

variable "pubsub_dead_letter_max_attempts" {
  description = "Number of delivery attempts before a Pub/Sub message is routed to the dead-letter topic. 5 retries allow for transient Adobe/Vertex API failures with exponential backoff."
  type        = number
  default     = 5
}

variable "pubsub_message_retention_seconds" {
  description = "How long Pub/Sub retains undelivered messages (seconds). 604800 = 7 days — long enough for ops team to investigate and reprocess failed documents."
  type        = number
  default     = 604800 # 7 days
}

variable "pubsub_ack_deadline_seconds" {
  description = "Pub/Sub subscriber acknowledgment deadline (seconds). 600s = 10 minutes — conservative upper bound for Adobe Extract API, which can take 2–5 minutes for complex PDFs."
  type        = number
  default     = 600 # 10 minutes — Adobe Extract can be slow on complex PDFs
}

variable "gcr_hostname" {
  description = "Google Container Registry hostname. gcr.io is the global registry; use us.gcr.io to keep images in the US region and avoid cross-region egress charges."
  type        = string
  default     = "gcr.io"
}
