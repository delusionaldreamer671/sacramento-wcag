"""OpenTelemetry instrumentation for the WCAG remediation pipeline.

Provides distributed tracing across the conversion pipeline stages:
  Adobe Extract → Document Reconstruction → HTML Build → Output

Configuration via environment variables:
  OTEL_ENABLED=true           — Enable tracing (default: true)
  OTEL_EXPORTER=console       — "console" (terminal) or "otlp" (Jaeger/collector)
  OTEL_ENDPOINT=http://...    — OTLP endpoint (default: http://localhost:4318/v1/traces)
  OTEL_SERVICE_NAME=wcag-...  — Service name in traces

Usage:
    from services.common.telemetry import init_telemetry, get_tracer
    init_telemetry(app)  # Call once at FastAPI startup
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("key", "value")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "wcag-remediation-pipeline")
_EXPORTER_TYPE = os.getenv("OTEL_EXPORTER", "console")  # "console" or "otlp"
_OTLP_ENDPOINT = os.getenv("OTEL_ENDPOINT", "http://localhost:4318/v1/traces")
_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")

_tracer_provider = None


def init_telemetry(app: FastAPI) -> None:
    """Initialize OpenTelemetry tracing and instrument FastAPI.

    Call this once during application startup. Sets up:
    - TracerProvider with resource attributes (service name, version)
    - Span exporter (console for dev, OTLP for Jaeger/collector)
    - FastAPI auto-instrumentation (HTTP spans for all endpoints)
    """
    global _tracer_provider

    if not _ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=%s)", os.getenv("OTEL_ENABLED"))
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        resource = Resource.create({
            "service.name": _SERVICE_NAME,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })

        _tracer_provider = TracerProvider(resource=resource)

        if _EXPORTER_TYPE == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                otlp_exporter = OTLPSpanExporter(endpoint=_OTLP_ENDPOINT)
                _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info("OTLP exporter configured: endpoint=%s", _OTLP_ENDPOINT)
            except Exception as exc:
                logger.warning("OTLP exporter failed (%s), falling back to console", exc)
                _tracer_provider.add_span_processor(
                    SimpleSpanProcessor(ConsoleSpanExporter())
                )
        else:
            _tracer_provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter())
            )
            logger.info("Console span exporter configured")

        trace.set_tracer_provider(_tracer_provider)

        # Bridge OTel trace context into Python log records
        try:
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
            LoggingInstrumentor().instrument(set_logging_format=False)
            logger.info("OTel log bridge enabled (trace_id/span_id injected into log records)")
        except Exception as log_exc:
            logger.warning("OTel log bridge failed (%s), logs won't have trace_id", log_exc)

        # Auto-instrument FastAPI
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,docs,redoc,openapi.json",
        )
        logger.info(
            "OpenTelemetry initialized: service=%s exporter=%s",
            _SERVICE_NAME, _EXPORTER_TYPE,
        )

    except ImportError as exc:
        logger.warning("OpenTelemetry packages not installed (%s). Tracing disabled.", exc)
    except Exception as exc:
        logger.warning("OpenTelemetry initialization failed (%s). Tracing disabled.", exc)


def get_tracer(name: str):
    """Get a tracer instance for manual span creation.

    Returns a real tracer if OTel is initialized, or a no-op tracer otherwise.
    """
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        # Return a no-op object that supports context manager usage
        return _NoOpTracer()


class _NoOpSpan:
    """No-op span for when OpenTelemetry is not available."""
    def set_attribute(self, key, value): pass
    def set_status(self, status): pass
    def record_exception(self, exc): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _NoOpTracer:
    """No-op tracer for when OpenTelemetry is not available."""
    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()


def shutdown() -> None:
    """Flush and shut down the tracer provider."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
        logger.info("OpenTelemetry shut down")
