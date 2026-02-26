"""Prometheus custom business metrics for the WCAG pipeline."""
try:
    from prometheus_client import Counter, Histogram, Gauge

    DOCUMENTS_PROCESSED = Counter(
        "wcag_documents_processed_total",
        "Total documents processed",
        ["status"],  # success, error, blocked
    )
    PIPELINE_DURATION = Histogram(
        "wcag_pipeline_duration_seconds",
        "Pipeline stage duration in seconds",
        ["stage"],  # extract, ai_alt_text, build_html, validate, output
        buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 300],
    )
    ACTIVE_PROCESSING = Gauge(
        "wcag_active_processing",
        "Number of documents currently being processed",
    )
    VALIDATION_SCORE = Gauge(
        "wcag_validation_score",
        "Last document validation score (0-100)",
    )
except ImportError:
    # Prometheus not available - create no-op metrics

    class _NoOpMetric:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def dec(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

    DOCUMENTS_PROCESSED = _NoOpMetric()
    PIPELINE_DURATION = _NoOpMetric()
    ACTIVE_PROCESSING = _NoOpMetric()
    VALIDATION_SCORE = _NoOpMetric()
