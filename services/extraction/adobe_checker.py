"""Adobe PDF Accessibility Checker API wrapper.

Calls the Adobe Acrobat Services PDF Accessibility Checker API
to validate PDF/UA compliance on output documents.

Uses the same Adobe credentials as the Extract API:
    WCAG_ADOBE_CLIENT_ID
    WCAG_ADOBE_CLIENT_SECRET

Reference: https://developer.adobe.com/document-services/docs/overview/pdf-accessibility-checker-api/
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from services.common.config import settings

logger = logging.getLogger(__name__)

_SDK_AVAILABLE = False

try:
    from adobe.pdfservices.operation.auth.service_principal_credentials import (
        ServicePrincipalCredentials,
    )
    from adobe.pdfservices.operation.pdf_services import PDFServices
    _SDK_AVAILABLE = True
except ImportError:
    pass

# HIGH-4.12: Check if the Accessibility Checker Job class is importable
# at module load time and set a flag. This avoids repeated try/except
# on every call and makes the checker's availability explicit.
_CHECKER_AVAILABLE = False
_PDFAccessibilityCheckerJob: Any = None
_CheckerResultType: Any = None

try:
    from adobe.pdfservices.operation.pdfjobs.jobs.pdf_accessibility_checker_job import (
        PDFAccessibilityCheckerJob as _ImportedCheckerJob,
    )
    _PDFAccessibilityCheckerJob = _ImportedCheckerJob
    # Check if result_type() is a valid callable
    if hasattr(_ImportedCheckerJob, "result_type") and callable(
        getattr(_ImportedCheckerJob, "result_type", None)
    ):
        _CheckerResultType = _ImportedCheckerJob.result_type()
    _CHECKER_AVAILABLE = True
except (ImportError, AttributeError, TypeError) as _checker_err:
    logger.info(
        "PDFAccessibilityCheckerJob not available in this SDK version: %s",
        _checker_err,
    )


class AdobeAccessibilityChecker:
    """Validates PDF/UA compliance via Adobe PDF Accessibility Checker API."""

    def __init__(self) -> None:
        if not _SDK_AVAILABLE:
            raise RuntimeError(
                "Adobe PDF Services SDK not installed. "
                "Run: pip install pdfservices-sdk"
            )

        if not settings.adobe_client_id or not settings.adobe_client_secret:
            raise ValueError(
                "Adobe credentials not configured. "
                "Set WCAG_ADOBE_CLIENT_ID and WCAG_ADOBE_CLIENT_SECRET."
            )

        credentials = ServicePrincipalCredentials(
            client_id=settings.adobe_client_id,
            client_secret=settings.adobe_client_secret,
        )
        self._pdf_services = PDFServices(credentials=credentials)
        logger.info("AdobeAccessibilityChecker initialized")

    def check_pdf(self, pdf_bytes: bytes) -> dict[str, Any]:
        """Run accessibility check on a PDF.

        Returns:
            {
                "compliant": bool,
                "issues": [
                    {
                        "rule": str,
                        "description": str,
                        "severity": "critical" | "serious" | "moderate" | "minor",
                        "page": int | None,
                    }
                ],
                "score": float,  # 0.0-1.0
                "check_failed": bool,  # True when result is due to an error, not actual check
            }

        Note: This is a stub implementation. The actual Adobe PDF
        Accessibility Checker API integration requires the
        PDFAccessibilityChecker operation which may not be available
        in all SDK versions. When unavailable, returns a basic result.
        """
        # HIGH-4.12: If the checker is not available, return immediately
        if not _CHECKER_AVAILABLE:
            logger.warning(
                "PDFAccessibilityCheckerJob not available in this SDK version. "
                "Returning basic compliance result."
            )
            return {
                "compliant": False,
                "issues": [{"rule": "sdk_unavailable", "description": "Adobe Accessibility Checker SDK not available — compliance unknown", "severity": "moderate", "page": None}],
                "score": 0.0,
                "check_failed": True,
                "note": "Full Adobe check unavailable — SDK version does not support PDFAccessibilityCheckerJob",
            }

        # HIGH-4.7: Retry loop (up to 2 retries) for transient API errors
        max_retries = 2
        last_exc: Exception | None = None
        tmp_path: str | None = None

        for attempt in range(1, max_retries + 2):
            try:
                # HIGH-4.7: Fix file handle leak — use context manager
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name

                # Use context manager to avoid leaking the file handle
                with open(tmp_path, "rb") as pdf_file:
                    input_asset = self._pdf_services.upload(
                        input_stream=pdf_file,
                        mime_type="application/pdf",
                    )

                job = _PDFAccessibilityCheckerJob(input_asset=input_asset)
                location = self._pdf_services.submit(job)

                # HIGH-4.12: Use pre-validated result type
                if _CheckerResultType is not None:
                    response = self._pdf_services.get_job_result(
                        location, _CheckerResultType,
                    )
                else:
                    # Fallback: try passing the class itself
                    response = self._pdf_services.get_job_result(
                        location, _PDFAccessibilityCheckerJob,
                    )

                # Parse the checker report
                report = response.get_result().get_report()
                return self._parse_report(report)

            except Exception as exc:
                last_exc = exc
                if attempt <= max_retries:
                    wait = 2.0 ** attempt
                    logger.warning(
                        "Adobe Accessibility Checker attempt %d/%d failed: %s. "
                        "Retrying in %.1fs.",
                        attempt, max_retries + 1, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    break
            finally:
                # Clean up temp file
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    tmp_path = None

        # HIGH-4.7: Return score 0.0 (not 0.5) with check_failed flag
        logger.warning("Adobe Accessibility Checker failed after retries: %s", last_exc)
        return {
            "compliant": False,
            "issues": [{
                "rule": "checker_error",
                "description": f"Adobe checker failed: {last_exc}",
                "severity": "moderate",
                "page": None,
            }],
            "score": 0.0,
            "check_failed": True,
        }

    def _parse_report(self, report: Any) -> dict[str, Any]:
        """Parse Adobe Accessibility Checker report into normalized format."""
        issues: list[dict[str, Any]] = []

        if hasattr(report, "get_issues"):
            for issue in report.get_issues():
                severity = "moderate"
                if hasattr(issue, "get_severity"):
                    raw = issue.get_severity().lower()
                    if "critical" in raw:
                        severity = "critical"
                    elif "serious" in raw or "error" in raw:
                        severity = "serious"
                    elif "warning" in raw:
                        severity = "moderate"
                    else:
                        severity = "minor"

                issues.append({
                    "rule": getattr(issue, "get_rule", lambda: "unknown")(),
                    "description": getattr(issue, "get_description", lambda: "")(),
                    "severity": severity,
                    "page": getattr(issue, "get_page", lambda: None)(),
                })

        total_checks = max(len(issues) + 10, 10)  # Rough estimate
        score = 1.0 - (len(issues) / total_checks) if total_checks > 0 else 1.0

        return {
            "compliant": len(issues) == 0,
            "issues": issues,
            "score": round(max(0.0, score), 4),
        }
