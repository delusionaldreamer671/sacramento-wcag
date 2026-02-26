"""Environment configuration for all pipeline services.

Reads settings from environment variables with sensible defaults
for local development. Production values set via Cloud Run env vars.
"""

from __future__ import annotations

import logging
import warnings

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings

_config_logger = logging.getLogger(__name__)


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
    adobe_client_secret: SecretStr = SecretStr("")

    # Vertex AI
    vertex_ai_model: str = "gemini-2.5-pro"
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
    regression_gate_blocking: bool = False
    alt_text_hitl_enabled: bool = True

    # Extraction cache — disabled by default to ensure fresh Adobe API calls
    extraction_cache_enabled: bool = False

    # Visual fidelity — preserve source font/style from the PDF
    preserve_source_styles: bool = True

    # Database
    db_path: str = "wcag_pipeline.db"
    db_backend: str = "sqlite"        # "sqlite" or "postgres"
    postgres_url: str = ""            # e.g. "postgresql://user:pass@host:5432/dbname"

    # Auth tokens (POC — seeded via env vars)
    admin_token: SecretStr = SecretStr("")
    reviewer_token: SecretStr = SecretStr("")

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

    # CORS
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:3001,https://hitl-dashboard.vercel.app"

    # Security
    rate_limit_per_minute: int = 60
    rate_limit_upload_per_minute: int = 10
    trusted_hosts: str = "*"

    # axesSense (PAC equivalent) validation
    axessense_url: str = ""
    axessense_api_key: str = ""
    axessense_enabled: bool = False

    model_config = {"env_prefix": "WCAG_", "env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_config(self) -> "Settings":
        # Hard errors for invalid combinations
        if self.db_backend == "postgres" and not self.postgres_url:
            raise ValueError(
                "db_backend is 'postgres' but postgres_url is empty. "
                "Set WCAG_POSTGRES_URL or switch to WCAG_DB_BACKEND=sqlite."
            )
        if self.db_backend not in ("sqlite", "postgres"):
            raise ValueError(
                f"db_backend must be 'sqlite' or 'postgres', got '{self.db_backend}'."
            )
        # Soft warnings for missing optional config
        if not self.adobe_client_id:
            warnings.warn(
                "WCAG_ADOBE_CLIENT_ID is empty — Adobe API calls will fail.",
                stacklevel=2,
            )
        if not self.admin_token.get_secret_value():
            warnings.warn(
                "WCAG_ADMIN_TOKEN is empty — admin auth will be unavailable.",
                stacklevel=2,
            )
        return self


settings = Settings()
