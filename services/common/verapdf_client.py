"""VeraPDF REST API client for PDF/UA-1 validation.

Wraps the verapdf/rest Docker container's REST endpoint.
Provides graceful degradation when the container is not running.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VeraPDFRuleFailure(BaseModel):
    """A single failing rule from VeraPDF validation."""
    rule_id: str = ""
    clause: str = ""
    description: str = ""
    test_number: int = 0
    failure_count: int = 0


class VeraPDFResult(BaseModel):
    """Parsed result from VeraPDF PDF/UA-1 validation."""
    is_compliant: bool = False
    total_rules_checked: int = 0
    failed_rules: list[VeraPDFRuleFailure] = Field(default_factory=list)
    passed_rules: int = 0
    error_count: int = 0
    failed_clauses: list[str] = Field(default_factory=list)
    raw_response: dict = Field(default_factory=dict)


class VeraPDFClient:
    """REST client for VeraPDF Docker container."""

    def __init__(self, base_url: str | None = None) -> None:
        if base_url is None:
            try:
                from services.common.config import Settings
                settings = Settings()
                base_url = settings.verapdf_url
            except Exception:
                base_url = "http://localhost:8080"
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

    def is_available(self) -> bool:
        """Check if VeraPDF container is reachable and healthy.

        MEDIUM-4.14: Check for HTTP 200 specifically, not just < 500.
        A 404 on /api/info means the container is not properly configured.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/api/info",
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            logger.debug("VeraPDF not reachable at %s", self._base_url)
            return False

    def validate_pdfua1(self, pdf_bytes: bytes) -> VeraPDFResult | None:
        """Validate PDF against PDF/UA-1 profile.

        Returns None if the container is unavailable or validation fails.

        HIGH-4.11: Retries 5xx responses (up to 2 retries, 2s backoff).
        Distinguishes 4xx (permanent, no retry) from 5xx (transient, retry).
        """
        max_retries = 2
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 2):
            try:
                resp = httpx.post(
                    f"{self._base_url}/api/validate/ua1",
                    content=pdf_bytes,
                    headers={"Content-Type": "application/pdf"},
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    return self._parse_response(resp.json())

                # 5xx: transient server error — retry
                if resp.status_code >= 500 and attempt <= max_retries:
                    logger.warning(
                        "VeraPDF returned HTTP %d (attempt %d/%d). "
                        "Retrying in %.1fs.",
                        resp.status_code, attempt, max_retries + 1,
                        2.0 * attempt,
                    )
                    time.sleep(2.0 * attempt)
                    continue

                # 4xx or final 5xx: permanent or exhausted retries
                logger.warning(
                    "VeraPDF returned HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
                return None

            except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                if attempt <= max_retries:
                    logger.warning(
                        "VeraPDF connection failed (attempt %d/%d): %s. "
                        "Retrying in %.1fs.",
                        attempt, max_retries + 1, exc, 2.0 * attempt,
                    )
                    time.sleep(2.0 * attempt)
                    continue
                logger.debug("VeraPDF unavailable after retries: %s", exc)
                return None
            except Exception as exc:
                logger.warning("VeraPDF validation error: %s", exc)
                return None

        # Should not reach here, but be safe
        return None

    def _parse_response(self, data: dict) -> VeraPDFResult:
        """Parse VeraPDF JSON response into VeraPDFResult."""
        # VeraPDF REST response structure varies by version.
        # Common structure: {"report": {"jobs": [{"validationResult": {...}}]}}
        # or direct: {"compliant": ..., "ruleSummaries": [...]}

        result = VeraPDFResult(raw_response=data)

        # Try direct response format
        if "compliant" in data:
            result.is_compliant = bool(data.get("compliant", False))
            summaries = data.get("ruleSummaries", [])
            return self._parse_rule_summaries(result, summaries)

        # Try nested report format
        report = data.get("report", {})
        jobs = report.get("jobs", [])
        if jobs:
            job = jobs[0]
            val_result = job.get("validationResult", {})
            result.is_compliant = bool(val_result.get("compliant", False))
            details = val_result.get("details", {})
            rules = details.get("rules", []) if isinstance(details, dict) else []
            return self._parse_rules(result, rules)

        # Try batchSummary format
        batch = report.get("batchSummary", {})
        val_summary = batch.get("validationReports", {})
        result.is_compliant = val_summary.get("compliant", 0) > 0
        result.error_count = val_summary.get("nonCompliant", 0)

        return result

    def _parse_rule_summaries(
        self, result: VeraPDFResult, summaries: list[dict]
    ) -> VeraPDFResult:
        failed = []
        clauses = set()
        for s in summaries:
            failed_checks = s.get("failedChecks", 0)
            if failed_checks > 0:
                clause = s.get("clause", s.get("specification", ""))
                desc = s.get("description", "")
                failure = VeraPDFRuleFailure(
                    rule_id=s.get("ruleId", ""),
                    clause=clause,
                    description=desc,
                    failure_count=failed_checks,
                )
                failed.append(failure)
                if clause:
                    clauses.add(clause)
        result.failed_rules = failed
        result.failed_clauses = sorted(clauses)
        result.error_count = sum(f.failure_count for f in failed)
        result.total_rules_checked = len(summaries)
        result.passed_rules = result.total_rules_checked - len(failed)
        return result

    def _parse_rules(
        self, result: VeraPDFResult, rules: list[dict]
    ) -> VeraPDFResult:
        failed = []
        clauses = set()
        passed = 0
        for r in rules:
            status = r.get("status", "")
            if status == "failed":
                clause = r.get("clause", r.get("specification", ""))
                desc = r.get("description", "")
                test_num = r.get("testNumber", 0)
                fail_count = r.get("failedChecks", 1)
                rule_id_raw = r.get("ruleId", "")
                rule_id = (
                    rule_id_raw.get("id", "")
                    if isinstance(rule_id_raw, dict)
                    else str(rule_id_raw)
                )
                failure = VeraPDFRuleFailure(
                    rule_id=rule_id,
                    clause=clause,
                    description=desc,
                    test_number=test_num,
                    failure_count=fail_count,
                )
                failed.append(failure)
                if clause:
                    clauses.add(clause)
            else:
                passed += 1
        result.failed_rules = failed
        result.failed_clauses = sorted(clauses)
        result.error_count = sum(f.failure_count for f in failed)
        result.total_rules_checked = len(rules)
        result.passed_rules = passed
        return result
