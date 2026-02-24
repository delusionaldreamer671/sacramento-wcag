# Sacramento County WCAG PDF Remediation Pipeline
# Multi-stage Docker build for all four Cloud Run services.
#
# Each service (ingestion, extraction, ai-drafting, recompilation) is built
# from this single Dockerfile. The target service is selected at build time
# via the SERVICE_NAME build argument. Cloud Build passes this per service.
#
# Build example:
#   docker build --build-arg SERVICE_NAME=ingestion -t sacto-wcag-ingestion .
#
# Cloud Run sets the PORT environment variable automatically (usually 8080).
# uvicorn binds to $PORT so the container works correctly without hardcoding.
#
# Security notes:
#   - Runs as non-root user (wcag) — principle of least privilege
#   - No secrets baked into the image — all credentials injected via env vars at runtime
#   - python:3.11-slim base minimises attack surface vs. full python:3.11

###############################################################################
# Stage 1: Builder
# Install Python dependencies into a virtual environment.
# Keeping this in a separate stage avoids shipping build tools (gcc, pip) in
# the final image, reducing image size by ~200MB.
###############################################################################
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system build dependencies required by some Python packages
# (e.g., pdfservices-sdk may need libssl, libffi for cryptographic operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install wheel/setuptools first — avoids legacy build issues
# with packages that don't ship pre-built wheels for python:3.11-slim
RUN pip install --upgrade pip setuptools wheel

# Copy requirements before copying source — this layer is cached as long as
# requirements.txt is unchanged, making subsequent builds fast.
COPY requirements.txt .

# Install all dependencies into a virtual environment at /opt/venv
# Using a venv keeps the site-packages isolated from the system Python,
# which simplifies COPY into the final stage.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

###############################################################################
# Stage 2: Runtime
# Minimal image with only the application code and installed packages.
###############################################################################
FROM python:3.11-slim AS runtime

# SERVICE_NAME build argument selects which service's entrypoint to use.
# Must be one of: ingestion, extraction, ai-drafting, recompilation
# This maps to the module at services/{SERVICE_NAME}/main.py
ARG SERVICE_NAME=ingestion

# Persist SERVICE_NAME as an env var so the startup script can use it.
# Also read by structured logging middleware to tag log lines with the service.
ENV SERVICE_NAME=${SERVICE_NAME}

# Install only runtime system dependencies — no build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # libssl and ca-certificates required for HTTPS connections to Adobe and GCP APIs
    libssl3 \
    ca-certificates \
    # curl is used by the health check smoke test in cloudbuild.yaml
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the application.
# Cloud Run runs as root by default — explicitly dropping to non-root
# reduces blast radius of any container escape vulnerability.
RUN groupadd --gid 1001 wcag && \
    useradd --uid 1001 --gid wcag --shell /bin/bash --create-home wcag

# Copy the virtual environment from the builder stage.
# /opt/venv is self-contained — no system Python packages needed.
COPY --from=builder /opt/venv /opt/venv

# Add the venv to PATH so `python` and `uvicorn` resolve without full paths
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory before copying application code
WORKDIR /app

# Copy the services directory into /app.
# All four services share the services/common/ package, so the entire
# services/ tree is copied. Each service entrypoint is at:
#   services/{service_name}/main.py → the FastAPI app object named `app`
COPY services/ ./services/

# Ensure the wcag user owns all application files
RUN chown -R wcag:wcag /app

# Switch to non-root user for all subsequent commands and the runtime process
USER wcag

# Cloud Run injects PORT at container start. Default to 8080 for local dev.
# Do NOT hardcode 8080 in the CMD — always read $PORT to be Cloud Run compatible.
ENV PORT=8080

# Python environment settings:
#   PYTHONUNBUFFERED=1  — flush stdout/stderr immediately (required for Cloud Run logging)
#   PYTHONDONTWRITEBYTECODE=1 — skip .pyc files (not needed in containers)
#   PYTHONPATH=/app    — ensures `from services.common.models import ...` resolves correctly
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Health check executed by Docker and Cloud Build smoke tests.
# Cloud Run has its own liveness probe (configured in Terraform) — this is for
# local `docker run` and Cloud Build post-deploy verification.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Expose the port for documentation purposes.
# Cloud Run ignores EXPOSE — it always routes to $PORT — but local `docker run`
# uses this to map ports automatically with `docker run -P`.
EXPOSE 8080

# Start the FastAPI application with uvicorn.
#
# Module path: services.{SERVICE_NAME}.main:app
#   - services.ingestion.main:app
#   - services.extraction.main:app
#   - services.ai_drafting.main:app   (note: hyphen → underscore for Python module)
#   - services.recompilation.main:app
#
# --host 0.0.0.0  — bind to all interfaces (required for Cloud Run to route traffic)
# --port $PORT    — Cloud Run sets PORT; must not hardcode 8080
# --workers 1     — Cloud Run autoscales instances, not workers. One worker per instance
#                   keeps memory predictable and avoids Pub/Sub consumer duplication.
# --log-level info — structured logging at info level for production
#
# Note: SERVICE_NAME uses underscores for Python module resolution.
#       The build arg accepts hyphens (ai-drafting) but we convert here with bash substitution.
CMD ["sh", "-c", "MODULE=$(echo $SERVICE_NAME | tr '-' '_') && exec uvicorn services.${MODULE}.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info"]
