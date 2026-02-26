"""axesSense REST API client for Matterhorn Protocol validation.

axesSense is the commercial REST API from axes4 GmbH (creators of PAC).
It performs the same 89 machine-testable Matterhorn Protocol checks as
the PAC desktop application but via a REST interface.

Provides graceful degradation when the API is unavailable — the pipeline
continues without axesSense results (identical to the VeraPDF pattern).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MatterhornRuleResult(BaseModel):
    """A single Matterhorn Protocol rule result."""

    rule_id: str = ""
    clause: str = ""
    description: str = ""
    status: str = ""  # "pass", "fail", "not_applicable"
    failure_count: int = 0
    details: list[str] = Field(default_factory=list)


class AxesSenseResult(BaseModel):
    """Parsed result from axesSense Matterhorn Protocol validation."""

    is_compliant: bool = False
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    not_applicable_checks: int = 0
    rule_results: list[MatterhornRuleResult] = Field(default_factory=list)
    raw_response: dict = Field(default_factory=dict)


class AxesSenseClient:
    """REST client for the axesSense Matterhorn Protocol API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if base_url is None or api_key is None:
            try:
                from services.common.config import settings

                base_url = base_url or settings.axessense_url
                api_key = api_key or settings.axessense_api_key
            except Exception:
                base_url = base_url or ""
                api_key = api_key or ""
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._api_key = api_key or ""
        self._timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

    def is_available(self) -> bool:
        """Check if axesSense API is reachable, configured, and authenticated.

        MEDIUM-4.13: Check for HTTP 200 specifically, not just < 500.
        A 401 response means the API key is invalid — the service is not
        usable even though it is reachable.
        """
        if not self._base_url or not self._api_key:
            return False
        try:
            resp = httpx.get(
                f"{self._base_url}/api/v1/health",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            logger.debug("axesSense not reachable at %s", self._base_url)
            return False

    def validate_matterhorn(self, pdf_bytes: bytes) -> AxesSenseResult | None:
        """Validate PDF against Matterhorn Protocol (89 machine-testable conditions).

        Returns None if the API is unavailable or validation fails.
        Never raises — follows the same graceful-degradation pattern as VeraPDF.
        """
        if not self._base_url or not self._api_key:
            logger.debug("axesSense not configured (no URL or API key)")
            return None

        try:
            resp = httpx.post(
                f"{self._base_url}/api/v1/validate",
                headers={"Authorization": f"Bearer {self._api_key}"},
                content=pdf_bytes,
                timeout=self._timeout,
            )

            if resp.status_code != 200:
                logger.warning(
                    "axesSense returned HTTP %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            data = resp.json()
            return self._parse_response(data)

        except httpx.TimeoutException:
            logger.warning("axesSense validation timed out")
            return None
        except (httpx.ConnectError, OSError) as exc:
            logger.warning("axesSense connection failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("axesSense validation error: %s", exc)
            return None

    def _parse_response(self, data: dict[str, Any]) -> AxesSenseResult:
        """Parse axesSense API response into structured result.

        Handles multiple possible response formats defensively.
        """
        rule_results: list[MatterhornRuleResult] = []
        passed = 0
        failed = 0
        na = 0

        # Try standard response format
        checks = data.get("checks", data.get("rules", data.get("results", [])))

        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                status = check.get("status", check.get("result", "")).lower()
                rule = MatterhornRuleResult(
                    rule_id=str(check.get("rule_id", check.get("id", ""))),
                    clause=str(check.get("clause", "")),
                    description=str(check.get("description", check.get("message", ""))),
                    status=status,
                    failure_count=int(check.get("failure_count", check.get("count", 0))),
                    details=check.get("details", []),
                )
                rule_results.append(rule)

                if status == "pass":
                    passed += 1
                elif status == "fail":
                    failed += 1
                elif status in ("not_applicable", "na", "skip"):
                    na += 1

        is_compliant = data.get("compliant", data.get("is_compliant", failed == 0))
        total = passed + failed + na

        return AxesSenseResult(
            is_compliant=bool(is_compliant),
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            not_applicable_checks=na,
            rule_results=rule_results,
            raw_response=data,
        )
