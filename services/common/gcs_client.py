"""Google Cloud Storage client utilities.

Provides async-friendly wrappers around GCS operations for
uploading, downloading, and managing PDF documents and extraction results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from google.cloud import storage

from services.common.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> storage.Client:
    return storage.Client(project=settings.gcp_project_id)


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

    blob.upload_from_filename(str(local_path), content_type=content_type)
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

    blob.upload_from_string(data, content_type=content_type)
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
    blob.download_to_filename(str(local_path))
    logger.info("Downloaded gs://%s/%s to %s", bucket_name, blob_name, local_path)
    return local_path


def download_bytes(bucket_name: str, blob_name: str) -> bytes:
    """Download a GCS object as bytes."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


def delete_blob(bucket_name: str, blob_name: str) -> None:
    """Delete a GCS object."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.delete()
    logger.info("Deleted gs://%s/%s", bucket_name, blob_name)


def list_blobs(bucket_name: str, prefix: str = "") -> list[str]:
    """List blob names in a bucket with optional prefix filter."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    return [blob.name for blob in bucket.list_blobs(prefix=prefix)]


def blob_exists(bucket_name: str, blob_name: str) -> bool:
    """Check if a blob exists in GCS."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    return bucket.blob(blob_name).exists()


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parse a gs://bucket/blob URI into (bucket_name, blob_name)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    parts = uri[5:].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid GCS URI (missing blob path): {uri}")
    return parts[0], parts[1]
