###############################################################################
# Sacramento County WCAG PDF Remediation Pipeline
# Terraform — GCP Infrastructure
#
# Resources provisioned:
#   - Google Cloud Storage (3 buckets: input, extraction, output)
#   - Cloud Pub/Sub (4 topics + 3 subscriptions + dead-letter config)
#   - Cloud Run (4 services: ingestion, extraction, ai-drafting, recompilation)
#   - IAM bindings (Cloud Run invoker, Pub/Sub publisher/subscriber)
#   - Service Account for pipeline workloads
#
# Secrets are NEVER stored in Terraform. All sensitive values (Adobe API keys,
# Vertex AI credentials) are injected at deploy time via Cloud Run env vars
# sourced from Secret Manager references or CI/CD environment variables.
#
# Prerequisites:
#   1. GCP project exists and billing is enabled
#   2. Required APIs enabled: run.googleapis.com, pubsub.googleapis.com,
#      storage.googleapis.com, aiplatform.googleapis.com, iam.googleapis.com
#   3. Docker image pushed to GCR before `terraform apply`
###############################################################################

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Remote state backend — replace with your GCS bucket after initial bootstrap.
  # For local dev, comment out this block and use local state.
  # backend "gcs" {
  #   bucket = "sacto-wcag-tf-state"
  #   prefix = "terraform/state"
  # }
}

###############################################################################
# Provider
###############################################################################

provider "google" {
  project = var.project_id
  region  = var.region

  # Authentication: set GOOGLE_APPLICATION_CREDENTIALS env var to the path of
  # your service account JSON key. Never commit the key file to version control.
}

###############################################################################
# Local computed values
# Centralise naming conventions so all resources share a consistent prefix.
# Format: sacto-wcag-{environment}-{resource}
###############################################################################

locals {
  # Short prefix used in resource names
  prefix = "sacto-wcag-${var.environment}"

  # Container image base path — shared across all Cloud Run services.
  # Each service appends its own name: gcr.io/PROJECT/sacto-wcag-ingestion:TAG
  image_base = "${var.gcr_hostname}/${var.project_id}"

  # WCAG_ prefix matches the pydantic-settings env_prefix in services/common/config.py
  # so all env vars are automatically picked up by Settings() without any code changes.
  common_env_vars = {
    WCAG_GCP_PROJECT_ID            = var.project_id
    WCAG_GCP_REGION                = var.region
    WCAG_GCS_INPUT_BUCKET          = google_storage_bucket.input.name
    WCAG_GCS_EXTRACTION_BUCKET     = google_storage_bucket.extraction.name
    WCAG_GCS_OUTPUT_BUCKET         = google_storage_bucket.output.name
    WCAG_PUBSUB_EXTRACTION_TOPIC   = google_pubsub_topic.document_extraction.name
    WCAG_PUBSUB_AI_DRAFTING_TOPIC  = google_pubsub_topic.ai_drafting.name
    WCAG_PUBSUB_RECOMPILATION_TOPIC = google_pubsub_topic.recompilation.name
    WCAG_PUBSUB_DEAD_LETTER_TOPIC  = google_pubsub_topic.dead_letter.name
    WCAG_PUBSUB_EXTRACTION_SUBSCRIPTION    = google_pubsub_subscription.extraction_sub.name
    WCAG_PUBSUB_AI_DRAFTING_SUBSCRIPTION   = google_pubsub_subscription.ai_drafting_sub.name
    WCAG_PUBSUB_RECOMPILATION_SUBSCRIPTION = google_pubsub_subscription.recompilation_sub.name
    WCAG_VERTEX_AI_LOCATION        = var.region
    WCAG_VERTEX_AI_MODEL           = "gemini-1.5-pro-002"
    # Retry config: 3 retries matches the NFR; base 2.0 gives 2s/4s/8s backoff
    WCAG_MAX_RETRIES               = "3"
    WCAG_RETRY_BACKOFF_BASE        = "2.0"
    WCAG_MAX_CONCURRENT_DOCUMENTS  = "100"
  }
}

###############################################################################
# Service Account
# A single pipeline service account with least-privilege IAM bindings below.
# Cloud Run services run as this identity.
###############################################################################

resource "google_service_account" "pipeline" {
  account_id   = "${local.prefix}-pipeline"
  display_name = "Sacramento WCAG Pipeline — ${var.environment}"
  description  = "Service account for WCAG remediation pipeline Cloud Run services. Grants access to GCS, Pub/Sub, and Vertex AI only."
  project      = var.project_id
}

###############################################################################
# GCS Buckets
#
# Three buckets mirror the pipeline stages:
#   input       — original uploaded PDFs (write-once, read-many)
#   extraction  — intermediate Adobe Extract JSON and Auto-Tag results
#   output      — final PDF/UA compliant documents delivered to county staff
#
# Uniform bucket-level access (no ACLs) — IAM only.
# Versioning enabled on output bucket for audit trail of remediated documents.
###############################################################################

resource "google_storage_bucket" "input" {
  name          = "${local.prefix}-input"
  location      = var.gcs_location
  project       = var.project_id
  force_destroy = var.environment != "prod" # Protect prod bucket from accidental deletion

  # Prevent public access — all county document data is sensitive
  public_access_prevention = "enforced"

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      # Input PDFs older than 90 days move to Nearline storage (lower cost, same durability)
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  labels = {
    environment = var.environment
    project     = "sacto-wcag"
    pipeline    = "input"
  }
}

resource "google_storage_bucket" "extraction" {
  name          = "${local.prefix}-extraction"
  location      = var.gcs_location
  project       = var.project_id
  force_destroy = var.environment != "prod"

  public_access_prevention    = "enforced"
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      # Intermediate extraction artifacts are transient — expire after 30 days
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    environment = var.environment
    project     = "sacto-wcag"
    pipeline    = "extraction"
  }
}

resource "google_storage_bucket" "output" {
  name          = "${local.prefix}-output"
  location      = var.gcs_location
  project       = var.project_id
  force_destroy = false # Never force-destroy output — these are official remediated documents

  public_access_prevention    = "enforced"
  uniform_bucket_level_access = true

  # Versioning: retain prior versions of remediated PDFs for audit compliance
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      # Keep non-current (superseded) versions for 365 days, then delete
      num_newer_versions = 3
      age                = 365
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    environment = var.environment
    project     = "sacto-wcag"
    pipeline    = "output"
  }
}

###############################################################################
# IAM — GCS
# Pipeline service account needs read/write on all buckets.
# objectAdmin covers create, read, update, delete on objects (not bucket metadata).
###############################################################################

resource "google_storage_bucket_iam_member" "pipeline_input_admin" {
  bucket = google_storage_bucket.input.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "pipeline_extraction_admin" {
  bucket = google_storage_bucket.extraction.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "pipeline_output_admin" {
  bucket = google_storage_bucket.output.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

###############################################################################
# Pub/Sub Topics
#
# Pipeline message flow:
#   ingestion → [document-extraction topic]
#     → extraction service → [ai-drafting topic]
#       → ai-drafting service → [recompilation topic]
#         → recompilation service → output bucket
#
#   Any service failure after max_delivery_attempts → [document-dead-letter topic]
###############################################################################

resource "google_pubsub_topic" "document_extraction" {
  name    = "document-extraction"
  project = var.project_id

  # 7-day message retention gives ops team time to recover from extended outages
  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  labels = {
    environment = var.environment
    pipeline    = "extraction"
  }
}

resource "google_pubsub_topic" "ai_drafting" {
  name    = "ai-drafting"
  project = var.project_id

  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  labels = {
    environment = var.environment
    pipeline    = "ai-drafting"
  }
}

resource "google_pubsub_topic" "recompilation" {
  name    = "recompilation"
  project = var.project_id

  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  labels = {
    environment = var.environment
    pipeline    = "recompilation"
  }
}

resource "google_pubsub_topic" "dead_letter" {
  name    = "document-dead-letter"
  project = var.project_id

  # Dead-letter messages retained longer — ops team may need to diagnose root cause
  # before reprocessing. 30 days = 2592000 seconds.
  message_retention_duration = "2592000s"

  labels = {
    environment = var.environment
    pipeline    = "dead-letter"
  }
}

###############################################################################
# Pub/Sub Subscriptions
#
# Each processing service has exactly one pull subscription on its input topic.
# Dead-letter policy routes messages that fail > max_delivery_attempts to the
# dead-letter topic rather than blocking the subscription indefinitely.
#
# ack_deadline_seconds = 600 (10 minutes) because Adobe Extract API for a
# complex 50-page PDF can take 3–5 minutes to return results.
###############################################################################

resource "google_pubsub_subscription" "extraction_sub" {
  name    = "document-extraction-sub"
  topic   = google_pubsub_topic.document_extraction.id
  project = var.project_id

  # 10-minute ack window covers Adobe Extract API latency for large documents
  ack_deadline_seconds = var.pubsub_ack_deadline_seconds

  # Retain undelivered messages for 7 days (matches topic retention)
  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.pubsub_dead_letter_max_attempts
  }

  retry_policy {
    # Exponential backoff: starts at 10s, caps at 600s (10 min)
    # Allows Adobe/Vertex transient errors to resolve between retries
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  labels = {
    environment = var.environment
    service     = "extraction"
  }

  depends_on = [google_pubsub_topic.dead_letter]
}

resource "google_pubsub_subscription" "ai_drafting_sub" {
  name    = "ai-drafting-sub"
  topic   = google_pubsub_topic.ai_drafting.id
  project = var.project_id

  ack_deadline_seconds = var.pubsub_ack_deadline_seconds

  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.pubsub_dead_letter_max_attempts
  }

  retry_policy {
    # Vertex AI Gemini can experience quota exhaustion — longer backoff gives
    # quota time to replenish before the next retry attempt
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  labels = {
    environment = var.environment
    service     = "ai-drafting"
  }

  depends_on = [google_pubsub_topic.dead_letter]
}

resource "google_pubsub_subscription" "recompilation_sub" {
  name    = "recompilation-sub"
  topic   = google_pubsub_topic.recompilation.id
  project = var.project_id

  ack_deadline_seconds = var.pubsub_ack_deadline_seconds

  message_retention_duration = "${var.pubsub_message_retention_seconds}s"

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.pubsub_dead_letter_max_attempts
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  labels = {
    environment = var.environment
    service     = "recompilation"
  }

  depends_on = [google_pubsub_topic.dead_letter]
}

###############################################################################
# IAM — Pub/Sub
# Pipeline SA needs:
#   - publisher on all pipeline topics (services publish to downstream topics)
#   - subscriber on all subscriptions (services pull messages from their topic)
#   - subscriber on dead-letter topic (ops tooling reads failed messages)
###############################################################################

resource "google_pubsub_topic_iam_member" "pipeline_extraction_publisher" {
  topic   = google_pubsub_topic.document_extraction.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "pipeline_ai_drafting_publisher" {
  topic   = google_pubsub_topic.ai_drafting.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "pipeline_recompilation_publisher" {
  topic   = google_pubsub_topic.recompilation.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "pipeline_dead_letter_publisher" {
  # The Pub/Sub service itself needs publisher rights on the dead-letter topic
  # to forward failed messages. This grants it to the pipeline SA as well for
  # any manual dead-letter re-publishing during incident response.
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
  project = var.project_id
}

resource "google_pubsub_subscription_iam_member" "pipeline_extraction_subscriber" {
  subscription = google_pubsub_subscription.extraction_sub.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.pipeline.email}"
  project      = var.project_id
}

resource "google_pubsub_subscription_iam_member" "pipeline_ai_drafting_subscriber" {
  subscription = google_pubsub_subscription.ai_drafting_sub.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.pipeline.email}"
  project      = var.project_id
}

resource "google_pubsub_subscription_iam_member" "pipeline_recompilation_subscriber" {
  subscription = google_pubsub_subscription.recompilation_sub.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.pipeline.email}"
  project      = var.project_id
}

###############################################################################
# IAM — Vertex AI
# aiplatform.user grants ability to call prediction endpoints (Gemini 1.5 Pro).
# Does NOT grant model management or billing visibility.
###############################################################################

resource "google_project_iam_member" "pipeline_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

###############################################################################
# Cloud Run — Ingestion Service
#
# Entry point for the pipeline. Receives PDF uploads via HTTP POST,
# validates the file, uploads to input GCS bucket, and publishes a
# document ID to the document-extraction Pub/Sub topic.
#
# Exposed publicly (or behind IAP — see variables for auth config).
# All other Cloud Run services are internal (no public ingress).
###############################################################################

resource "google_cloud_run_v2_service" "ingestion" {
  name     = "${local.prefix}-ingestion"
  location = var.region
  project  = var.project_id

  # ingestion is the only public-facing service; all others use INTERNAL ingress
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.pipeline.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      # Image built by cloudbuild.yaml and pushed to GCR
      image = "${local.image_base}/sacto-wcag-ingestion:${var.container_image_tag}"

      resources {
        limits = {
          # Ingestion is lightweight — it only validates and uploads PDFs
          cpu    = "1"
          memory = "512Mi"
        }
        # cpu_idle = true allows CPU to be throttled between requests (cost saving)
        cpu_idle = true
      }

      # Cloud Run sets PORT automatically; uvicorn binds to it
      ports {
        container_port = 8080
        name           = "http1"
      }

      dynamic "env" {
        for_each = local.common_env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      # Service-specific env: WCAG_SERVICE_NAME is read by structured logging middleware
      env {
        name  = "WCAG_SERVICE_NAME"
        value = "ingestion"
      }

      # Adobe credentials are injected from Secret Manager at deploy time.
      # Never hardcoded — sourced from CI/CD pipeline secrets or manual
      # `gcloud run services update --update-secrets` commands.
      # These env names match pydantic-settings fields in services/common/config.py.
      env {
        name  = "WCAG_ADOBE_CLIENT_ID"
        value = "" # Replaced at deploy time: --update-secrets WCAG_ADOBE_CLIENT_ID=adobe-client-id:latest
      }

      env {
        name  = "WCAG_ADOBE_CLIENT_SECRET"
        value = "" # Replaced at deploy time: --update-secrets WCAG_ADOBE_CLIENT_SECRET=adobe-client-secret:latest
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        # Allow up to 60s for cold start (pip-installed packages take time to import)
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 5
      }
    }

    max_instance_request_concurrency = var.cloud_run_concurrency
  }

  labels = {
    environment = var.environment
    service     = "ingestion"
    project     = "sacto-wcag"
  }
}

###############################################################################
# Cloud Run — Extraction Service
#
# Pub/Sub push subscriber (or pull consumer). Calls Adobe Acrobat Services
# Extract API + Auto-Tag API. Stores results in extraction GCS bucket.
# Internal only — not reachable from the internet.
###############################################################################

resource "google_cloud_run_v2_service" "extraction" {
  name     = "${local.prefix}-extraction"
  location = var.region
  project  = var.project_id

  # Internal ingress: only reachable from within the VPC and GCP services (Pub/Sub push)
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.pipeline.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      image = "${local.image_base}/sacto-wcag-extraction:${var.container_image_tag}"

      resources {
        limits = {
          # Adobe Extract processes PDF pages in memory; 2GB handles up to ~200 pages
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
        name           = "http1"
      }

      dynamic "env" {
        for_each = local.common_env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "WCAG_SERVICE_NAME"
        value = "extraction"
      }

      env {
        name  = "WCAG_ADOBE_CLIENT_ID"
        value = ""
      }

      env {
        name  = "WCAG_ADOBE_CLIENT_SECRET"
        value = ""
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 5
      }
    }

    max_instance_request_concurrency = var.cloud_run_concurrency
  }

  labels = {
    environment = var.environment
    service     = "extraction"
    project     = "sacto-wcag"
  }
}

###############################################################################
# Cloud Run — AI Drafting Service
#
# Pub/Sub consumer. Calls Vertex AI Gemini 1.5 Pro to generate:
#   - Alt text for images (WCAG 1.1.1)
#   - Semantic HTML table structure (WCAG 1.3.1)
# Stores HITLReviewItem records and flags REVIEW/MANUAL items.
# Internal only.
###############################################################################

resource "google_cloud_run_v2_service" "ai_drafting" {
  name     = "${local.prefix}-ai-drafting"
  location = var.region
  project  = var.project_id

  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.pipeline.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      image = "${local.image_base}/sacto-wcag-ai-drafting:${var.container_image_tag}"

      resources {
        limits = {
          # Vertex AI SDK is memory-hungry for concurrent requests; 4GB with 4 CPUs
          # allows safe parallel Gemini calls for multi-image documents
          cpu    = "4"
          memory = "4Gi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
        name           = "http1"
      }

      dynamic "env" {
        for_each = local.common_env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "WCAG_SERVICE_NAME"
        value = "ai-drafting"
      }

      # ai_drafting_timeout_seconds controls how long we wait for a single Gemini
      # response before treating it as a failure and flagging as MANUAL.
      # 60s matches the NFR: "< 60s per PDF page for extraction + AI drafting".
      env {
        name  = "WCAG_AI_DRAFTING_TIMEOUT_SECONDS"
        value = "60"
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 5
      }
    }

    max_instance_request_concurrency = var.cloud_run_concurrency
  }

  labels = {
    environment = var.environment
    service     = "ai-drafting"
    project     = "sacto-wcag"
  }
}

###############################################################################
# Cloud Run — Recompilation Service
#
# Pub/Sub consumer. Assembles approved HITLReviewItems into semantic HTML,
# runs axe-core validation, generates PDF/UA output, stores in output bucket.
# Internal only.
###############################################################################

resource "google_cloud_run_v2_service" "recompilation" {
  name     = "${local.prefix}-recompilation"
  location = var.region
  project  = var.project_id

  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.pipeline.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      image = "${local.image_base}/sacto-wcag-recompilation:${var.container_image_tag}"

      resources {
        limits = {
          # PDF generation is CPU-intensive; 4 CPUs accelerates Adobe PDF Services calls
          cpu    = "4"
          memory = "4Gi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
        name           = "http1"
      }

      dynamic "env" {
        for_each = local.common_env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "WCAG_SERVICE_NAME"
        value = "recompilation"
      }

      env {
        name  = "WCAG_ADOBE_CLIENT_ID"
        value = ""
      }

      env {
        name  = "WCAG_ADOBE_CLIENT_SECRET"
        value = ""
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 5
      }
    }

    max_instance_request_concurrency = var.cloud_run_concurrency
  }

  labels = {
    environment = var.environment
    service     = "recompilation"
    project     = "sacto-wcag"
  }
}

###############################################################################
# IAM — Cloud Run Invoker
#
# The ingestion service URL is invokable by all authenticated users (or
# specific groups — replace allUsers with the county IAP service account for
# production deployments).
#
# Internal services (extraction, ai-drafting, recompilation) are invoked only
# by the pipeline service account via Pub/Sub push subscriptions.
###############################################################################

resource "google_cloud_run_v2_service_iam_member" "ingestion_invoker" {
  name     = google_cloud_run_v2_service.ingestion.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"

  # In production, replace "allUsers" with the county IAP principal:
  # "serviceAccount:service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com"
  # For POC, this allows unauthenticated access to the upload endpoint only.
  member = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "extraction_invoker" {
  name     = google_cloud_run_v2_service.extraction.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"

  # Only the pipeline service account may invoke internal services.
  # Pub/Sub push subscriptions authenticate using this identity.
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_cloud_run_v2_service_iam_member" "ai_drafting_invoker" {
  name     = google_cloud_run_v2_service.ai_drafting.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_cloud_run_v2_service_iam_member" "recompilation_invoker" {
  name     = google_cloud_run_v2_service.recompilation.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}
