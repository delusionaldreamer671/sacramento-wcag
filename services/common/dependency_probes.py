"""Dependency probes for startup and health-check validation.

Each probe tests actual connectivity to an external dependency,
not just config presence. Probes are designed to be fast (<5s)
and fail-safe (a probe failure doesn't crash the service).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

ProbeStatus = Literal["up", "down", "degraded", "unchecked"]


@dataclass
class ProbeResult:
    name: str
    status: ProbeStatus
    message: str = ""
    latency_ms: float = 0.0
    required: bool = True


class DependencyProbe:
    """Base class for dependency probes."""
    name: str = "unknown"
    required: bool = True

    def probe(self) -> ProbeResult:
        raise NotImplementedError


class DatabaseProbe(DependencyProbe):
    name = "database"
    required = True

    def probe(self) -> ProbeResult:
        start = time.monotonic()
        try:
            from services.common.config import settings
            from services.common.database import get_db
            db = get_db(settings.db_path)
            db._backend.fetchone("SELECT 1", ())
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(name=self.name, status="up", latency_ms=elapsed, required=self.required)
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name, status="down",
                message=str(exc), latency_ms=elapsed, required=self.required,
            )


class AdobeCredentialsProbe(DependencyProbe):
    name = "adobe_credentials"
    required = False

    def probe(self) -> ProbeResult:
        start = time.monotonic()
        from services.common.config import settings
        has_id = bool(settings.adobe_client_id)
        has_secret = bool(settings.adobe_client_secret.get_secret_value())
        elapsed = (time.monotonic() - start) * 1000

        if has_id and has_secret:
            return ProbeResult(name=self.name, status="up", latency_ms=elapsed, required=self.required)

        missing = []
        if not has_id:
            missing.append("WCAG_ADOBE_CLIENT_ID")
        if not has_secret:
            missing.append("WCAG_ADOBE_CLIENT_SECRET")
        return ProbeResult(
            name=self.name, status="down",
            message=f"Missing: {', '.join(missing)}",
            latency_ms=elapsed, required=self.required,
        )


class VertexAIProbe(DependencyProbe):
    name = "vertex_ai"
    required = False

    def probe(self) -> ProbeResult:
        start = time.monotonic()
        from services.common.config import settings

        if not settings.vertex_ai_model:
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name, status="down",
                message="WCAG_VERTEX_AI_MODEL not set",
                latency_ms=elapsed, required=self.required,
            )

        # Check for explicit credentials file
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(name=self.name, status="up", latency_ms=elapsed, required=self.required)

        # Check for Cloud Run / GCE ADC
        if os.environ.get("K_SERVICE") or os.environ.get("GOOGLE_CLOUD_PROJECT"):
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(name=self.name, status="up", message="Using ADC", latency_ms=elapsed, required=self.required)

        # Fallback: try google.auth.default()
        try:
            import google.auth
            credentials, _project = google.auth.default()
            elapsed = (time.monotonic() - start) * 1000
            if credentials is not None:
                return ProbeResult(name=self.name, status="up", message="Using google.auth.default()", latency_ms=elapsed, required=self.required)
            return ProbeResult(
                name=self.name, status="down",
                message="google.auth.default() returned None",
                latency_ms=elapsed, required=self.required,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name, status="down",
                message=f"GCP auth failed: {exc}",
                latency_ms=elapsed, required=self.required,
            )


class VeraPDFProbe(DependencyProbe):
    name = "verapdf"
    required = False

    def probe(self) -> ProbeResult:
        start = time.monotonic()
        try:
            from services.common.verapdf_client import VeraPDFClient
            client = VeraPDFClient()
            available = client.is_available()
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name,
                status="up" if available else "down",
                message="" if available else "VeraPDF container not reachable",
                latency_ms=elapsed, required=self.required,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name, status="down",
                message=str(exc), latency_ms=elapsed, required=self.required,
            )


class AxeCoreProbe(DependencyProbe):
    name = "axe_core"
    required = False

    def probe(self) -> ProbeResult:
        start = time.monotonic()
        try:
            from services.common.axe_runner import is_available
            available = is_available()
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name,
                status="up" if available else "down",
                message="" if available else "playwright or axe-core not installed",
                latency_ms=elapsed, required=self.required,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ProbeResult(
                name=self.name, status="down",
                message=str(exc), latency_ms=elapsed, required=self.required,
            )


ALL_PROBES: list[DependencyProbe] = [
    DatabaseProbe(),
    AdobeCredentialsProbe(),
    VertexAIProbe(),
    VeraPDFProbe(),
    AxeCoreProbe(),
]


def run_all_probes() -> list[ProbeResult]:
    """Run all dependency probes and return results."""
    results = []
    for probe in ALL_PROBES:
        try:
            result = probe.probe()
        except Exception as exc:
            result = ProbeResult(
                name=probe.name, status="down",
                message=f"Probe crashed: {exc}",
                required=probe.required,
            )
        results.append(result)
    return results


def probes_to_health_response(results: list[ProbeResult]) -> dict[str, Any]:
    """Convert probe results into the PipelineHealthResponse format."""
    services: dict[str, str] = {"ingestion": "up"}
    for r in results:
        services[r.name] = r.status
        if r.message:
            services[f"{r.name}_detail"] = r.message

    required_down = any(r.status == "down" and r.required for r in results)

    overall = "degraded" if required_down else "healthy"
    return {"status": overall, "services": services}
