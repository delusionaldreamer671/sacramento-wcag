###############################################################################
# Sacramento County WCAG PDF Remediation Pipeline
# Terraform Outputs
#
# These values are available after `terraform apply` via:
#   terraform output                    # all outputs
#   terraform output ingestion_url      # specific output
#   terraform output -json              # JSON format for scripting
#
# Sensitive outputs (service account email, project-level details) are marked
# sensitive = true and will not print unless explicitly requested with -raw flag.
###############################################################################

###############################################################################
# Cloud Run Service URLs
###############################################################################

output "ingestion_service_url" {
  description = "Public HTTPS URL for the ingestion service. This is the entry point for PDF uploads. Route county staff document submission tooling to this URL."
  value       = google_cloud_run_v2_service.ingestion.uri
}

output "extraction_service_url" {
  description = "Internal HTTPS URL for the extraction service. Not publicly reachable — invoked only by Pub/Sub push subscriptions using the pipeline service account."
  value       = google_cloud_run_v2_service.extraction.uri
}

output "ai_drafting_service_url" {
  description = "Internal HTTPS URL for the AI drafting service. Not publicly reachable — invoked only by Pub/Sub push subscriptions using the pipeline service account."
  value       = google_cloud_run_v2_service.ai_drafting.uri
}

output "recompilation_service_url" {
  description = "Internal HTTPS URL for the recompilation service. Not publicly reachable — invoked only by Pub/Sub push subscriptions using the pipeline service account."
  value       = google_cloud_run_v2_service.recompilation.uri
}

###############################################################################
# Pub/Sub Topic Names
# Used to configure Pub/Sub push subscriptions and publisher IAM bindings
# in downstream tooling (e.g., the HITL dashboard backend, monitoring alerts).
###############################################################################

output "pubsub_extraction_topic_name" {
  description = "Pub/Sub topic name for document extraction messages. Ingestion service publishes here after a successful PDF upload."
  value       = google_pubsub_topic.document_extraction.name
}

output "pubsub_extraction_topic_id" {
  description = "Fully qualified Pub/Sub topic ID (projects/PROJECT/topics/NAME) for use in publisher/subscriber client code."
  value       = google_pubsub_topic.document_extraction.id
}

output "pubsub_ai_drafting_topic_name" {
  description = "Pub/Sub topic name for AI drafting messages. Extraction service publishes here after successful structural extraction."
  value       = google_pubsub_topic.ai_drafting.name
}

output "pubsub_ai_drafting_topic_id" {
  description = "Fully qualified Pub/Sub topic ID for the AI drafting topic."
  value       = google_pubsub_topic.ai_drafting.id
}

output "pubsub_recompilation_topic_name" {
  description = "Pub/Sub topic name for recompilation messages. AI drafting service publishes here when all HITL review items are approved."
  value       = google_pubsub_topic.recompilation.name
}

output "pubsub_recompilation_topic_id" {
  description = "Fully qualified Pub/Sub topic ID for the recompilation topic."
  value       = google_pubsub_topic.recompilation.id
}

output "pubsub_dead_letter_topic_name" {
  description = "Pub/Sub dead-letter topic name. Failed messages (after max_delivery_attempts) are routed here. Monitor this topic for pipeline failures."
  value       = google_pubsub_topic.dead_letter.name
}

output "pubsub_dead_letter_topic_id" {
  description = "Fully qualified Pub/Sub topic ID for the dead-letter topic. Subscribe to this for alerting on pipeline failures."
  value       = google_pubsub_topic.dead_letter.id
}

###############################################################################
# Pub/Sub Subscription Names
###############################################################################

output "pubsub_extraction_subscription_name" {
  description = "Pub/Sub subscription name for the extraction service pull consumer."
  value       = google_pubsub_subscription.extraction_sub.name
}

output "pubsub_ai_drafting_subscription_name" {
  description = "Pub/Sub subscription name for the AI drafting service pull consumer."
  value       = google_pubsub_subscription.ai_drafting_sub.name
}

output "pubsub_recompilation_subscription_name" {
  description = "Pub/Sub subscription name for the recompilation service pull consumer."
  value       = google_pubsub_subscription.recompilation_sub.name
}

###############################################################################
# GCS Bucket Names
###############################################################################

output "gcs_input_bucket_name" {
  description = "GCS bucket name for incoming PDFs. The ingestion service uploads original county PDFs here before publishing to Pub/Sub."
  value       = google_storage_bucket.input.name
}

output "gcs_input_bucket_url" {
  description = "GCS URI for the input bucket (gs://NAME format). Use this in gsutil commands and GCS client SDK calls."
  value       = "gs://${google_storage_bucket.input.name}"
}

output "gcs_extraction_bucket_name" {
  description = "GCS bucket name for Adobe Extract and Auto-Tag JSON results. Intermediate artifacts — auto-deleted after 30 days."
  value       = google_storage_bucket.extraction.name
}

output "gcs_extraction_bucket_url" {
  description = "GCS URI for the extraction results bucket."
  value       = "gs://${google_storage_bucket.extraction.name}"
}

output "gcs_output_bucket_name" {
  description = "GCS bucket name for final PDF/UA output documents. Versioning enabled — previous versions retained for audit compliance."
  value       = google_storage_bucket.output.name
}

output "gcs_output_bucket_url" {
  description = "GCS URI for the output bucket. County staff download remediated PDFs from this bucket."
  value       = "gs://${google_storage_bucket.output.name}"
}

###############################################################################
# Service Account
###############################################################################

output "pipeline_service_account_email" {
  description = "Email of the pipeline service account. All Cloud Run services run as this identity. Use this when configuring external integrations that need to verify the pipeline's identity."
  value       = google_service_account.pipeline.email
  sensitive   = true # Contains project ID — suppress from default output
}

###############################################################################
# Environment Configuration Block
# Copy-paste ready env var block for local development and CI/CD.
# Paste into .env file or CI/CD secrets — never commit to version control.
###############################################################################

output "local_env_config" {
  description = "Environment variable block for local development. Run `terraform output -raw local_env_config > .env` to bootstrap your local environment. IMPORTANT: Add .env to .gitignore before running this command."
  sensitive   = true # Suppress from default output since it contains resource names that could aid enumeration

  value = <<-ENV
    # Sacramento WCAG Pipeline — generated by `terraform output -raw local_env_config`
    # DO NOT COMMIT THIS FILE — add .env to .gitignore
    WCAG_GCP_PROJECT_ID=${var.project_id}
    WCAG_GCP_REGION=${var.region}
    WCAG_GCS_INPUT_BUCKET=${google_storage_bucket.input.name}
    WCAG_GCS_EXTRACTION_BUCKET=${google_storage_bucket.extraction.name}
    WCAG_GCS_OUTPUT_BUCKET=${google_storage_bucket.output.name}
    WCAG_PUBSUB_EXTRACTION_TOPIC=${google_pubsub_topic.document_extraction.name}
    WCAG_PUBSUB_AI_DRAFTING_TOPIC=${google_pubsub_topic.ai_drafting.name}
    WCAG_PUBSUB_RECOMPILATION_TOPIC=${google_pubsub_topic.recompilation.name}
    WCAG_PUBSUB_DEAD_LETTER_TOPIC=${google_pubsub_topic.dead_letter.name}
    WCAG_PUBSUB_EXTRACTION_SUBSCRIPTION=${google_pubsub_subscription.extraction_sub.name}
    WCAG_PUBSUB_AI_DRAFTING_SUBSCRIPTION=${google_pubsub_subscription.ai_drafting_sub.name}
    WCAG_PUBSUB_RECOMPILATION_SUBSCRIPTION=${google_pubsub_subscription.recompilation_sub.name}
    WCAG_INGESTION_SERVICE_URL=${google_cloud_run_v2_service.ingestion.uri}
    WCAG_EXTRACTION_SERVICE_URL=${google_cloud_run_v2_service.extraction.uri}
    WCAG_AI_DRAFTING_SERVICE_URL=${google_cloud_run_v2_service.ai_drafting.uri}
    WCAG_RECOMPILATION_SERVICE_URL=${google_cloud_run_v2_service.recompilation.uri}
    # Fill in manually — never auto-generated:
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
    WCAG_ADOBE_CLIENT_ID=<your-adobe-client-id>
    WCAG_ADOBE_CLIENT_SECRET=<your-adobe-client-secret>
  ENV
}
