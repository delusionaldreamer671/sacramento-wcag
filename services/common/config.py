"""Environment configuration for all pipeline services.

Reads settings from environment variables with sensible defaults
for local development. Production values set via Cloud Run env vars.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # GCP
    gcp_project_id: str = "sacramento-wcag-poc"
    gcp_region: str = "us-central1"

    # GCS Buckets
    gcs_input_bucket: str = "sacto-wcag-input"
    gcs_extraction_bucket: str = "sacto-wcag-extraction"
    gcs_output_bucket: str = "sacto-wcag-output"

    # Pub/Sub Topics
    pubsub_extraction_topic: str = "document-extraction"
    pubsub_ai_drafting_topic: str = "ai-drafting"
    pubsub_recompilation_topic: str = "recompilation"

    # Pub/Sub Subscriptions
    pubsub_extraction_subscription: str = "document-extraction-sub"
    pubsub_ai_drafting_subscription: str = "ai-drafting-sub"
    pubsub_recompilation_subscription: str = "recompilation-sub"

    # Dead Letter
    pubsub_dead_letter_topic: str = "document-dead-letter"

    # Adobe Acrobat Services
    adobe_client_id: str = ""
    adobe_client_secret: str = ""

    # Vertex AI
    vertex_ai_model: str = "gemini-1.5-pro-002"
    vertex_ai_location: str = "us-central1"

    # Document AI (OCR)
    docai_processor_id: str = ""
    docai_location: str = "us"

    # Validation tooling
    axe_enabled: bool = True
    adobe_checker_enabled: bool = True

    # VeraPDF validation
    verapdf_url: str = "http://localhost:8080"
    verapdf_enabled: bool = True
    verapdf_timeout_seconds: int = 60

    # Extraction cache — disabled by default to ensure fresh Adobe API calls
    extraction_cache_enabled: bool = False

    # Database (SQLite for POC)
    db_path: str = "wcag_pipeline.db"

    # Auth tokens (POC — seeded via env vars)
    admin_token: str = ""
    reviewer_token: str = ""

    # OCR routing thresholds
    ocr_min_chars_threshold: int = 20

    # Service URLs (for inter-service communication)
    ingestion_service_url: str = "http://localhost:8000"
    extraction_service_url: str = "http://localhost:8001"
    ai_drafting_service_url: str = "http://localhost:8002"
    recompilation_service_url: str = "http://localhost:8003"

    # Retry configuration
    max_retries: int = 3
    retry_backoff_base: float = 2.0

    # Processing limits
    max_concurrent_documents: int = 100
    max_pages_per_document: int = 500
    ai_drafting_timeout_seconds: int = 60

    model_config = {"env_prefix": "WCAG_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
