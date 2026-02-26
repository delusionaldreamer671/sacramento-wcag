"""Google Cloud Storage client utilities.

Provides async-friendly wrappers around GCS operations for
uploading, downloading, and managing PDF documents and extraction results.

CRITICAL-4.3: Uses a module-level singleton client (thread-safe per Google
docs) instead of creating a new client per call. All blob operations include
explicit timeouts to prevent indefinite hangs.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from google.cloud import storage

from services.common.config import settings

logger = logging.getLogger(__name__)

# Default timeout (seconds) for GCS blob operations.
_GCS_TIMEOUT = 120

# Module-level singleton client with thread-safe lazy initialization.
_client: storage.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> storage.Client:
    """Return a module-level singleton GCS client (thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            # Double-checked locking
            if _client is None:
                _client = storage.Client(project=settings.gcp_project_id)
    return _client


def upload_file(
    local_path: str | Path,
    bucket_name: str,
    blob_name: str,
    content_type: Optional[str] = None,
) -> str:
    """Upload a local file to GCS. Returns the gs:// URI."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.upload_from_filename(
        str(local_path), content_type=content_type, timeout=_GCS_TIMEOUT
    )
    gcs_uri = f"gs://{bucket_name}/{blob_name}"
    logger.info("Uploaded %s to %s", local_path, gcs_uri)
    return gcs_uri


def upload_bytes(
    data: bytes,
    bucket_name: str,
    blob_name: str,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to GCS. Returns the gs:// URI."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(data, content_type=content_type, timeout=_GCS_TIMEOUT)
    gcs_uri = f"gs://{bucket_name}/{blob_name}"
    logger.info("Uploaded %d bytes to %s", len(data), gcs_uri)
    return gcs_uri


def download_file(bucket_name: str, blob_name: str, local_path: str | Path) -> Path:
    """Download a GCS object to a local file. Returns the local Path."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path), timeout=_GCS_TIMEOUT)
    logger.info("Downloaded gs://%s/%s to %s", bucket_name, blob_name, local_path)
    return local_path


def download_bytes(bucket_name: str, blob_name: str) -> bytes:
    """Download a GCS object as bytes."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes(timeout=_GCS_TIMEOUT)


def delete_blob(bucket_name: str, blob_name: str) -> None:
    """Delete a GCS object."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.delete(timeout=_GCS_TIMEOUT)
    logger.info("Deleted gs://%s/%s", bucket_name, blob_name)


def list_blobs(bucket_name: str, prefix: str = "") -> list[str]:
    """List blob names in a bucket with optional prefix filter."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    return [blob.name for blob in bucket.list_blobs(prefix=prefix, timeout=_GCS_TIMEOUT)]


def blob_exists(bucket_name: str, blob_name: str) -> bool:
    """Check if a blob exists in GCS."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    return bucket.blob(blob_name).exists(timeout=_GCS_TIMEOUT)


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parse a gs://bucket/blob URI into (bucket_name, blob_name)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    parts = uri[5:].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid GCS URI (missing blob path): {uri}")
    return parts[0], parts[1]
