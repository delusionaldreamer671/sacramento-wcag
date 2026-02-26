"""Security utilities for input validation and sanitization."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# PDF magic bytes: %PDF (may have BOM prefix)
_PDF_SIGNATURES = [b"%PDF", b"\xef\xbb\xbf%PDF"]
_MAX_FILENAME_LENGTH = 255
_SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9._\- ]")


def validate_pdf_bytes(data: bytes, filename: str = "unknown") -> None:
    """Validate that data looks like a PDF file.

    Raises ValueError if the data doesn't start with a PDF signature
    within the first 1024 bytes.
    """
    if not data:
        raise ValueError("File is empty")

    header = data[:1024]
    for sig in _PDF_SIGNATURES:
        if sig in header:
            return

    logger.warning(
        "Rejected non-PDF file: filename=%s first_bytes=%r",
        filename,
        data[:20],
    )
    raise ValueError(
        f"File '{filename}' does not appear to be a valid PDF. "
        "Expected PDF signature (%%PDF) in file header."
    )


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename for safe storage and display.

    - Strips path separators (/ and \\)
    - Removes special characters
    - Limits length to 255 characters
    - Preserves extension
    """
    # Strip path separators
    filename = filename.replace("/", "_").replace("\\", "_")

    # Remove unsafe characters, keep alphanumeric, dots, hyphens, underscores, spaces
    sanitized = _SAFE_FILENAME_PATTERN.sub("_", filename)

    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")

    # Limit length (preserve extension)
    if len(sanitized) > _MAX_FILENAME_LENGTH:
        ext_idx = sanitized.rfind(".")
        if ext_idx > 0:
            ext = sanitized[ext_idx:]
            sanitized = sanitized[:_MAX_FILENAME_LENGTH - len(ext)] + ext
        else:
            sanitized = sanitized[:_MAX_FILENAME_LENGTH]

    return sanitized or "document.pdf"


def check_file_size(data: bytes, max_bytes: int = 50 * 1024 * 1024) -> None:
    """Raise ValueError if file exceeds max size.

    Default max is 50MB.
    """
    if len(data) > max_bytes:
        size_mb = len(data) / (1024 * 1024)
        max_mb = max_bytes / (1024 * 1024)
        raise ValueError(
            f"File size ({size_mb:.1f}MB) exceeds maximum allowed ({max_mb:.0f}MB)."
        )
