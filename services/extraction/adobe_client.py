"""Adobe Acrobat Services wrapper for PDF extraction and auto-tagging.

Wraps the pdfservices-sdk (v4.x) to:
  - Extract structural content (text, tables, images) via the Extract API
  - Generate PDF tag structure via the Auto-Tag API

Both operations upload their result JSON to GCS and return GCS URIs.

Retry strategy: exponential backoff on 5xx or SDK ServiceApiException,
up to settings.max_retries attempts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from services.common import gcs_client
from services.common.config import settings
from services.common.telemetry import get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


# ---------------------------------------------------------------------------
# Adobe SDK imports — guarded so the module loads in test environments
# without the full SDK installed (unit tests can mock at the class level).
# ---------------------------------------------------------------------------

# --- Extract API imports (core — required) ---
try:
    from adobe.pdfservices.operation.auth.service_principal_credentials import (
        ServicePrincipalCredentials,
    )
    from adobe.pdfservices.operation.exception.exceptions import ServiceApiException
    from adobe.pdfservices.operation.io.cloud_asset import CloudAsset
    from adobe.pdfservices.operation.io.stream_asset import StreamAsset
    from adobe.pdfservices.operation.pdf_services import PDFServices
    from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
    from adobe.pdfservices.operation.pdfjobs.jobs.extract_pdf_job import ExtractPDFJob
    from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.extract_element_type import (
        ExtractElementType,
    )
    from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.extract_pdf_params import (
        ExtractPDFParams,
    )
    from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.extract_renditions_element_type import (
        ExtractRenditionsElementType,
    )
    from adobe.pdfservices.operation.pdfjobs.result.extract_pdf_result import (
        ExtractPDFResult,
    )

    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    ServiceApiException = Exception  # type: ignore[assignment, misc]
    logger.warning(
        "adobe.pdfservices SDK not installed. AdobeExtractClient will raise "
        "RuntimeError unless mocked."
    )

# --- Auto-Tag API imports (optional — not all SDK versions include these) ---
_AUTO_TAG_AVAILABLE = False
try:
    from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import AutotagPDFJob
    from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import (
        AutotagPDFParams,
    )
    from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import (
        AutotagPDFResult,
    )

    _AUTO_TAG_AVAILABLE = True
except ImportError:
    logger.info(
        "Adobe Auto-Tag API not available in this SDK version. "
        "Extract API will still work."
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cache version — bump when extraction logic changes to invalidate stale cache.
# This ensures new features (figure images, text cleaning) are not skipped
# by old cached results that don't include them.
#   v1: Initial extraction cache
#   v2: Added figure image extraction from Adobe ZIP (filePaths)
#   v3: Added PyMuPDF fallback image extraction, text spacing fixes
_CACHE_VERSION = "v3"

_EXTRACT_ELEMENTS = [
    "ExtractElementType.TEXT",
    "ExtractElementType.TABLES",
]

# MIME types for figure images extracted from Adobe ZIP
_FIGURE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _extract_figures_from_zip(zip_bytes: bytes) -> dict[str, str]:
    """Extract figure image files from Adobe Extract API ZIP and base64-encode them.

    The ZIP contains ``figures/fileoutpartN.png`` (or .jpg) files.
    Returns a dict mapping the relative path (e.g. ``"figures/fileoutpart0.png"``)
    to a ``"data:<mime>;base64,<data>"`` URI string ready for ``<img src=...>``.
    """
    figures: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for name in zf.namelist():
                if not name.lower().startswith("figures/"):
                    continue
                ext = Path(name).suffix.lower()
                mime = _FIGURE_MIME.get(ext)
                if not mime:
                    continue
                img_bytes = zf.read(name)
                if not img_bytes:
                    continue
                b64 = base64.b64encode(img_bytes).decode("ascii")
                figures[name] = f"data:{mime};base64,{b64}"
    except (zipfile.BadZipFile, OSError) as exc:
        logger.debug("ZIP extraction failed (non-fatal): %s", exc)
    return figures


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def _with_retry(
    fn: Any,
    max_retries: int,
    backoff_base: float,
    operation_name: str,
) -> Any:
    """Execute *fn()* with exponential backoff on ServiceApiException (5xx).

    Raises the final exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except ServiceApiException as exc:
            # Only retry on server-side errors (5xx-equivalent)
            status_code: int = getattr(exc, "status_code", 500) or 500
            if status_code < 500 and status_code != 0:
                logger.error(
                    "%s failed with non-retryable status %s: %s",
                    operation_name,
                    status_code,
                    exc,
                )
                raise
            last_exc = exc
            wait = backoff_base ** (attempt - 1)
            logger.warning(
                "%s attempt %d/%d failed (status=%s). Retrying in %.1fs. Error: %s",
                operation_name,
                attempt,
                max_retries,
                status_code,
                wait,
                exc,
            )
            time.sleep(wait)

    assert last_exc is not None
    logger.error("%s failed after %d retries.", operation_name, max_retries)
    raise last_exc


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AdobeExtractClient:
    """Wraps Adobe Acrobat Services Extract and Auto-Tag APIs.

    Args:
        client_id: Adobe API client ID (from env via settings).
        client_secret: Adobe API client secret (from env via settings).
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client_id = client_id or settings.adobe_client_id
        self._client_secret = client_secret or settings.adobe_client_secret
        self._max_retries = settings.max_retries
        self._backoff_base = settings.retry_backoff_base

        if not self._client_id or not self._client_secret:
            raise ValueError(
                "Adobe client_id and client_secret must be set via "
                "WCAG_ADOBE_CLIENT_ID / WCAG_ADOBE_CLIENT_SECRET env vars."
            )
        if not _SDK_AVAILABLE:
            raise RuntimeError(
                "pdfservices-sdk is not installed. "
                "Run: pip install pdfservices-sdk"
            )

    def _make_pdf_services(self) -> "PDFServices":
        credentials = ServicePrincipalCredentials(
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return PDFServices(credentials=credentials)

    # ------------------------------------------------------------------
    # Extract API
    # ------------------------------------------------------------------

    def extract_pdf(self, gcs_input_path: str) -> dict[str, Any]:
        """Run Adobe Extract API on a PDF stored in GCS.

        Downloads the PDF from GCS, submits it to the Extract API,
        uploads the resulting ZIP/JSON to GCS, and returns a dict with:
            - adobe_job_id: str
            - extracted_json_path: gs:// URI for the result JSON
            - elements_count, images_count, tables_count: ints

        Args:
            gcs_input_path: gs://bucket/blob URI of the source PDF.

        Returns:
            dict with extraction metadata (ready to build ExtractionResult).

        Raises:
            ServiceApiException: if Adobe API fails after max retries.
            ValueError: if GCS URI is invalid.
        """
        bucket_name, blob_name = gcs_client.parse_gcs_uri(gcs_input_path)
        doc_id = blob_name.split("/")[1] if "/" in blob_name else str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_pdf = Path(tmp_dir) / "input.pdf"
            logger.info("Downloading PDF from GCS: %s", gcs_input_path)
            gcs_client.download_file(bucket_name, blob_name, local_pdf)
            logger.info(
                "Downloaded PDF: size=%d bytes document_id=%s",
                local_pdf.stat().st_size,
                doc_id,
            )

            result_data = _with_retry(
                lambda: self._run_extract(local_pdf, tmp_dir),
                max_retries=self._max_retries,
                backoff_base=self._backoff_base,
                operation_name="Adobe Extract API",
            )

            # Upload result JSON to GCS
            result_json: dict[str, Any] = result_data["json"]
            json_bytes = json.dumps(result_json).encode("utf-8")
            extraction_blob = f"extraction/{doc_id}/extract_result.json"
            extracted_json_path = gcs_client.upload_bytes(
                data=json_bytes,
                bucket_name=settings.gcs_extraction_bucket,
                blob_name=extraction_blob,
                content_type="application/json",
            )
            logger.info(
                "Uploaded extraction result: path=%s", extracted_json_path
            )

        elements = result_json.get("elements", [])
        images_count = sum(
            1 for e in elements if e.get("Path", "").startswith("//Figure")
        )
        tables_count = sum(
            1 for e in elements if e.get("Path", "").startswith("//Table")
        )

        logger.info(
            "Extraction complete: document_id=%s elements=%d images=%d tables=%d",
            doc_id,
            len(elements),
            images_count,
            tables_count,
        )

        return {
            "adobe_job_id": result_data["job_id"],
            "extracted_json_path": extracted_json_path,
            "elements_count": len(elements),
            "images_count": images_count,
            "tables_count": tables_count,
        }

    def _run_extract(self, local_pdf: Path, output_dir: str) -> dict[str, Any]:
        """Internal: submit Extract job and collect results.

        Returns a dict with:
          - job_id: str
          - json: the structured extraction JSON
          - figure_images: dict mapping filePaths (e.g. "figures/fileoutpart0.png")
            to base64-encoded image data.  Empty if no figures or ZIP unavailable.
        """
        pdf_services = self._make_pdf_services()

        with open(local_pdf, "rb") as pdf_file:
            input_stream = pdf_file.read()

        input_asset = pdf_services.upload(
            input_stream=input_stream,
            mime_type=PDFServicesMediaType.PDF,
        )

        extract_params = ExtractPDFParams(
            elements_to_extract=[ExtractElementType.TEXT, ExtractElementType.TABLES],
            elements_to_extract_renditions=[ExtractRenditionsElementType.FIGURES],
            styling_info=True,  # Exposes TextSize, Font.weight, StartIndent, etc.
        )

        extract_job = ExtractPDFJob(
            input_asset=input_asset,
            extract_pdf_params=extract_params,
        )

        location = pdf_services.submit(extract_job)
        response = pdf_services.get_job_result(
            location, ExtractPDFResult
        )

        result = response.get_result()

        # Get the structured JSON
        result_json: dict[str, Any]
        try:
            content_json = result.get_content_json()
            if isinstance(content_json, dict):
                result_json = content_json
            else:
                result_json = json.loads(content_json)
        except (AttributeError, TypeError):
            result_asset = result.get_resource()
            result_stream = result_asset.get_input_stream()
            result_json = json.loads(result_stream.read())

        # Extract figure images from the resource ZIP
        figure_images: dict[str, str] = {}
        try:
            resource_asset = result.get_resource()
            if resource_asset is not None:
                zip_stream = resource_asset.get_input_stream()
                zip_bytes = zip_stream if isinstance(zip_stream, bytes) else zip_stream.read()
                figure_images = _extract_figures_from_zip(zip_bytes)
                if figure_images:
                    logger.info(
                        "Extracted %d figure images from Adobe ZIP",
                        len(figure_images),
                    )
        except Exception as exc:
            logger.debug(
                "Could not extract figures from ZIP (non-fatal): %s", exc
            )

        job_id: str = location.split("/")[-1] if location else str(uuid.uuid4())
        return {"job_id": job_id, "json": result_json, "figure_images": figure_images}

    # ------------------------------------------------------------------
    # Local path variants (no GCS — for sync converter)
    # ------------------------------------------------------------------

    # Persistent cache directory for extraction results.
    # Keyed by SHA-256 of PDF content, so the same file always hits cache
    # regardless of which temp directory it's copied into.
    _CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".extract_cache"

    def extract_pdf_from_path(self, local_pdf: Path) -> dict[str, Any]:
        """Run Adobe Extract API on a local PDF file (no GCS needed).

        Used by the synchronous converter endpoint for local testing.
        Returns the raw extraction JSON directly instead of uploading to GCS.

        **Figure images**: When the Adobe ZIP contains rendered figure images,
        they are extracted and stored under a ``_figure_images`` key in the
        returned dict.  Each entry maps a ``filePaths`` value (e.g.
        ``"figures/fileoutpart0.png"``) to a ``data:<mime>;base64,...`` URI.
        The converter can look up an element's ``filePaths[0]`` in this dict
        to get the actual image data — no PyMuPDF bounding-box guessing needed.

        **Caching**: Results are cached in ``.extract_cache/`` at the project
        root, keyed by SHA-256 hash of PDF content. Subsequent calls for the
        same PDF content (even from a temp directory) skip the API entirely,
        preserving free-tier quota. Figure images are cached separately.

        Args:
            local_pdf: Path to the PDF file on the local filesystem.

        Returns:
            dict — the raw Adobe Extract JSON with elements array and
            ``_figure_images`` dict.
        """
        with tracer.start_as_current_span("adobe.extract_pdf_from_path") as span:
            span.set_attribute("adobe.pdf_path", str(local_pdf))
            pdf_bytes = local_pdf.read_bytes()
            pdf_size = len(pdf_bytes)
            span.set_attribute("adobe.pdf_size_bytes", pdf_size)
            span.set_attribute("adobe.max_retries", self._max_retries)

            # --- Check persistent extraction cache ---
            content_hash = hashlib.sha256(pdf_bytes).hexdigest()
            self._CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path = self._CACHE_DIR / f"{content_hash}_{_CACHE_VERSION}.json"
            figures_cache_path = self._CACHE_DIR / f"{content_hash}_{_CACHE_VERSION}_figures.json"

            cache_enabled = settings.extraction_cache_enabled
            if cache_enabled and cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    elements = cached.get("elements", [])
                    span.set_attribute("adobe.cache_hit", True)
                    span.set_attribute("adobe.elements_count", len(elements))

                    # Load cached figure images if available
                    if figures_cache_path.exists():
                        try:
                            cached["_figure_images"] = json.loads(
                                figures_cache_path.read_text(encoding="utf-8")
                            )
                            span.set_attribute(
                                "adobe.cached_figures",
                                len(cached["_figure_images"]),
                            )
                        except (json.JSONDecodeError, OSError):
                            cached["_figure_images"] = {}
                    else:
                        cached["_figure_images"] = {}

                    logger.info(
                        "Cache HIT: %s (hash=%s, %d elements, %d figures, saved 1 API call)",
                        local_pdf.name, content_hash[:12], len(elements),
                        len(cached["_figure_images"]),
                    )
                    return cached
                except (json.JSONDecodeError, KeyError):
                    logger.debug("Cache file invalid, will re-extract: %s", cache_path)

            span.set_attribute("adobe.cache_hit", False)
            logger.info("Cache MISS: %s (hash=%s). Calling Adobe API.", local_pdf.name, content_hash[:12])

            result_data = _with_retry(
                lambda: self._run_extract(local_pdf, str(local_pdf.parent)),
                max_retries=self._max_retries,
                backoff_base=self._backoff_base,
                operation_name="Adobe Extract API (local)",
            )

            result_json: dict[str, Any] = result_data["json"]
            figure_images: dict[str, str] = result_data.get("figure_images", {})
            elements = result_json.get("elements", [])
            span.set_attribute("adobe.elements_count", len(elements))
            span.set_attribute("adobe.figure_images", len(figure_images))
            span.set_attribute("adobe.job_id", result_data.get("job_id", ""))
            logger.info(
                "Local extraction complete: elements=%d figures=%d path=%s",
                len(elements), len(figure_images), local_pdf,
            )

            # --- Write to persistent cache (only when enabled) ---
            if cache_enabled:
                try:
                    cache_path.write_text(
                        json.dumps(result_json, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info("Cached extraction result: %s", cache_path)
                except OSError as exc:
                    logger.warning("Could not write extraction cache: %s", exc)

                # Cache figure images separately (can be large)
                if figure_images:
                    try:
                        figures_cache_path.write_text(
                            json.dumps(figure_images, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        logger.info(
                            "Cached %d figure images: %s",
                            len(figure_images), figures_cache_path,
                        )
                    except OSError as exc:
                        logger.warning("Could not write figures cache: %s", exc)
            else:
                logger.info("Cache disabled — skipping cache write")

            # Attach figure images to the returned dict
            result_json["_figure_images"] = figure_images
            return result_json

    # ------------------------------------------------------------------
    # Auto-Tag API
    # ------------------------------------------------------------------

    def auto_tag_pdf(self, gcs_input_path: str) -> dict[str, Any]:
        """Run Adobe Auto-Tag API on a PDF stored in GCS.

        Downloads the PDF, submits it to the Auto-Tag API, uploads the
        resulting tagged PDF and report JSON to GCS, and returns a dict with:
            - auto_tag_json_path: gs:// URI for the tag report JSON
            - tag_count: number of tags in the structure

        Requires the Auto-Tag API classes to be available in the SDK.

        Args:
            gcs_input_path: gs://bucket/blob URI of the source PDF.

        Returns:
            dict with auto-tag metadata.

        Raises:
            ServiceApiException: if Adobe API fails after max retries.
            RuntimeError: if Auto-Tag API is not available in the SDK.
        """
        if not _AUTO_TAG_AVAILABLE:
            raise RuntimeError(
                "Adobe Auto-Tag API is not available in this SDK version. "
                "The Extract API still works for structural extraction."
            )
        bucket_name, blob_name = gcs_client.parse_gcs_uri(gcs_input_path)
        doc_id = blob_name.split("/")[1] if "/" in blob_name else str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_pdf = Path(tmp_dir) / "input.pdf"
            logger.info("Downloading PDF for auto-tagging: %s", gcs_input_path)
            gcs_client.download_file(bucket_name, blob_name, local_pdf)

            result_data = _with_retry(
                lambda: self._run_auto_tag(local_pdf),
                max_retries=self._max_retries,
                backoff_base=self._backoff_base,
                operation_name="Adobe Auto-Tag API",
            )

            # Upload tag report to GCS
            report_bytes = json.dumps(result_data["report"]).encode("utf-8")
            tag_blob = f"extraction/{doc_id}/auto_tag_result.json"
            auto_tag_json_path = gcs_client.upload_bytes(
                data=report_bytes,
                bucket_name=settings.gcs_extraction_bucket,
                blob_name=tag_blob,
                content_type="application/json",
            )
            logger.info("Uploaded auto-tag result: path=%s", auto_tag_json_path)

        tag_count = result_data.get("tag_count", 0)
        logger.info(
            "Auto-tagging complete: document_id=%s tags=%d", doc_id, tag_count
        )

        return {
            "auto_tag_json_path": auto_tag_json_path,
            "tag_count": tag_count,
        }

    def _run_auto_tag(self, local_pdf: Path) -> dict[str, Any]:
        """Internal: submit Auto-Tag job and collect results.

        Returns a dict with:
          - report: the Auto-Tag report JSON
          - tag_count: number of tags in the structure
          - tagged_pdf: bytes of the tagged PDF (the primary output)
        """
        pdf_services = self._make_pdf_services()

        with open(local_pdf, "rb") as pdf_file:
            input_stream = pdf_file.read()

        input_asset: CloudAsset = pdf_services.upload(
            input_stream=input_stream,  # type: ignore[arg-type]
            mime_type=PDFServicesMediaType.PDF,
        )

        auto_tag_params = (
            AutotagPDFParams.builder()
            .with_generate_report(True)
            .with_shift_headings(False)
            .build()
        )

        auto_tag_job = AutotagPDFJob(
            input_asset=input_asset,
            autotag_pdf_params=auto_tag_params,
        )

        location = pdf_services.submit(auto_tag_job)
        result: AutotagPDFResult = pdf_services.get_job_result(
            location, AutotagPDFResult
        )

        # Retrieve the tagged PDF (primary output)
        tagged_pdf_bytes: bytes = b""
        try:
            tagged_asset: StreamAsset = result.get_tagged_pdf()
            tagged_stream = tagged_asset.get_input_stream()
            tagged_pdf_bytes = (
                tagged_stream
                if isinstance(tagged_stream, bytes)
                else tagged_stream.read()
            )
            logger.info(
                "Auto-Tag: retrieved tagged PDF (%d bytes)", len(tagged_pdf_bytes)
            )
        except Exception as exc:
            logger.warning("Auto-Tag: could not retrieve tagged PDF: %s", exc)

        # Retrieve the report JSON
        report_asset: StreamAsset = result.get_report()
        report_stream = report_asset.get_input_stream()
        report_json: dict[str, Any] = json.loads(report_stream.read())

        tag_count = len(report_json.get("tags", []))
        return {
            "report": report_json,
            "tag_count": tag_count,
            "tagged_pdf": tagged_pdf_bytes,
        }

    # ------------------------------------------------------------------
    # Auto-Tag: local path variant (no GCS — for sync converter)
    # ------------------------------------------------------------------

    def auto_tag_pdf_from_path(self, local_pdf: Path) -> dict[str, Any]:
        """Run Adobe Auto-Tag API on a local PDF and return tagged PDF bytes.

        Used by the synchronous converter when output_format="pdf".
        Returns a dict with:
          - tagged_pdf: bytes of the tagged PDF (empty if API fails)
          - report: Auto-Tag report JSON
          - tag_count: number of tags

        **Caching**: Results are cached in ``.extract_cache/`` keyed by
        SHA-256 hash of PDF content with ``_autotag`` suffix.

        Args:
            local_pdf: Path to the source PDF.

        Returns:
            dict with tagged_pdf bytes, report, and tag_count.
        """
        if not _AUTO_TAG_AVAILABLE:
            logger.warning(
                "auto_tag_pdf_from_path: Auto-Tag API not available in SDK"
            )
            return {"tagged_pdf": b"", "report": {}, "tag_count": 0}

        with tracer.start_as_current_span("adobe.auto_tag_from_path") as span:
            span.set_attribute("adobe.pdf_path", str(local_pdf))
            pdf_bytes = local_pdf.read_bytes()
            span.set_attribute("adobe.pdf_size_bytes", len(pdf_bytes))

            # --- Check persistent cache ---
            content_hash = hashlib.sha256(pdf_bytes).hexdigest()
            self._CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_pdf_path = self._CACHE_DIR / f"{content_hash}_{_CACHE_VERSION}_autotag.pdf"
            cache_report_path = self._CACHE_DIR / f"{content_hash}_{_CACHE_VERSION}_autotag_report.json"

            cache_enabled = settings.extraction_cache_enabled
            if cache_enabled and cache_pdf_path.exists():
                try:
                    cached_pdf = cache_pdf_path.read_bytes()
                    cached_report: dict[str, Any] = {}
                    if cache_report_path.exists():
                        cached_report = json.loads(
                            cache_report_path.read_text(encoding="utf-8")
                        )
                    span.set_attribute("adobe.autotag_cache_hit", True)
                    logger.info(
                        "Auto-Tag cache HIT: %s (hash=%s, %d bytes)",
                        local_pdf.name,
                        content_hash[:12],
                        len(cached_pdf),
                    )
                    return {
                        "tagged_pdf": cached_pdf,
                        "report": cached_report,
                        "tag_count": len(cached_report.get("tags", [])),
                    }
                except (OSError, json.JSONDecodeError):
                    logger.debug("Auto-Tag cache invalid, will re-process")

            span.set_attribute("adobe.autotag_cache_hit", False)
            logger.info(
                "Auto-Tag cache MISS: %s (hash=%s). Calling Adobe API.",
                local_pdf.name,
                content_hash[:12],
            )

            result_data = _with_retry(
                lambda: self._run_auto_tag(local_pdf),
                max_retries=self._max_retries,
                backoff_base=self._backoff_base,
                operation_name="Adobe Auto-Tag API (local)",
            )

            tagged_pdf = result_data.get("tagged_pdf", b"")
            report = result_data.get("report", {})
            tag_count = result_data.get("tag_count", 0)

            span.set_attribute("adobe.autotag_pdf_size", len(tagged_pdf))
            span.set_attribute("adobe.autotag_tag_count", tag_count)

            # --- Write to persistent cache (only when enabled) ---
            if cache_enabled and tagged_pdf:
                try:
                    cache_pdf_path.write_bytes(tagged_pdf)
                    logger.info("Cached tagged PDF: %s", cache_pdf_path)
                except OSError as exc:
                    logger.warning("Could not cache tagged PDF: %s", exc)

            if cache_enabled and report:
                try:
                    cache_report_path.write_text(
                        json.dumps(report, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    logger.warning("Could not cache Auto-Tag report: %s", exc)

            logger.info(
                "Auto-Tag complete: %s — %d bytes tagged PDF, %d tags",
                local_pdf.name,
                len(tagged_pdf),
                tag_count,
            )
            return {
                "tagged_pdf": tagged_pdf,
                "report": report,
                "tag_count": tag_count,
            }
