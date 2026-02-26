FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Common fonts for PDF fidelity
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright Chromium browser is installed for the pip-installed version.
# The base image bundles browsers for its built-in playwright version, but pip
# may install a different version that needs its own browser binaries.
RUN playwright install chromium --with-deps

# Copy application code
COPY services/ services/
COPY schemas/ schemas/

# Create non-root user and writable data directory
RUN groupadd -g 1001 wcag && useradd -u 1001 -g wcag -m wcag \
    && mkdir -p /data && chown wcag:wcag /data
USER wcag

# Cloud Run sets PORT env var (default 8080)
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV WCAG_DB_PATH=/data/wcag_pipeline.db
ENV WCAG_EXTRACTION_CACHE_DIR=/data/.extract_cache

EXPOSE ${PORT}

CMD exec uvicorn services.ingestion.main:app --host 0.0.0.0 --port ${PORT}
