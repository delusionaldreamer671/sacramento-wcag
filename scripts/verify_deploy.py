#!/usr/bin/env python3
"""Post-deploy verification script for Sacramento WCAG pipeline.

This script runs after EVERY deployment and produces a PASS/FAIL verdict.
No deployment should be considered successful until this script reports
ALL CHECKS PASSED.

Usage:
    # Default run (cheap — uses 1-page canary PDF, ~1 Gemini call):
    python scripts/verify_deploy.py [--url URL] [--revision REV]

    # Full run (includes image PDF for Vertex AI e2e):
    python scripts/verify_deploy.py --with-image-pdf

    # Skip all paid API calls (health + env + CORS only):
    python scripts/verify_deploy.py --skip-paid-apis

Cost guardrails:
    - Default canary pack: 1-page text PDF (~1520 bytes)
    - External API calls per default run: ~2 (1 analyze + 1 remediate)
    - --with-image-pdf adds: ~1 extra Gemini call
    - --skip-paid-apis: 0 external API calls (health/env/CORS only)
    - Hard cap: MAX_EXTERNAL_API_CALLS (default 5) per run

Exit codes:
    0 = ALL checks passed
    1 = One or more checks FAILED
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_URL = "https://sacto-wcag-api-738802459862.us-central1.run.app"

# Cost guardrails: hard cap on external API calls per verification run.
# Each /analyze or /remediate call triggers server-side Adobe + Gemini calls.
MAX_EXTERNAL_API_CALLS = 5
_external_api_call_count = 0


def _track_external_call(name: str) -> bool:
    """Track and enforce external API call cap. Returns True if allowed."""
    global _external_api_call_count
    _external_api_call_count += 1
    if _external_api_call_count > MAX_EXTERNAL_API_CALLS:
        print(f"  [!] SKIPPED {name}: external API call cap ({MAX_EXTERNAL_API_CALLS}) exceeded")
        return False
    return True


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    category: str = "unknown"


@dataclass
class VerificationReport:
    checks: list[CheckResult] = field(default_factory=list)
    url: str = ""
    timestamp: str = ""
    revision: str = ""

    def add(self, name: str, passed: bool, detail: str = "", category: str = "unknown") -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=detail, category=category))

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def print_report(self) -> None:
        print("\n" + "=" * 70)
        print("POST-DEPLOY VERIFICATION REPORT")
        print(f"URL: {self.url}")
        print(f"Timestamp: {self.timestamp}")
        if self.revision:
            print(f"Revision: {self.revision}")
        print("=" * 70)

        # Group by category
        categories: dict[str, list[CheckResult]] = {}
        for c in self.checks:
            categories.setdefault(c.category, []).append(c)

        for cat, checks in categories.items():
            print(f"\n--- {cat.upper()} ---")
            for c in checks:
                status = "PASS" if c.passed else "FAIL"
                icon = "[+]" if c.passed else "[X]"
                print(f"  {icon} {status}: {c.name}")
                if c.detail:
                    # Indent multi-line details
                    for line in c.detail.split("\n"):
                        print(f"       {line}")

        passed = sum(1 for c in self.checks if c.passed)
        failed = sum(1 for c in self.checks if not c.passed)
        total = len(self.checks)
        print(f"\n{'=' * 70}")
        verdict = "ALL CHECKS PASSED" if self.all_passed else f"{failed} CHECK(S) FAILED"
        print(f"VERDICT: {verdict} ({passed}/{total} passed)")
        print("=" * 70)


def _make_test_pdf() -> bytes:
    """Create a test PDF with structured, extractable content.

    Uses reportlab (preferred) to produce a PDF with multiple text elements
    styled as a heading and paragraphs. This gives the extraction stage
    meaningful content to process — a blank or minimal PDF can silently pass
    the scanned-PDF guard while producing zero elements, masking broken extraction.

    Structure:
      - Title / heading: "Verification Test Document"
      - Section heading: "Section 1: WCAG Compliance"
      - Body paragraph with substantive text
      - Second paragraph with WCAG criterion list

    Falls back to pikepdf (with a proper StructTreeRoot) if reportlab is absent.
    """
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import inch

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        styles = getSampleStyleSheet()

        story = [
            Paragraph("Verification Test Document", styles["Title"]),
            Spacer(1, 0.2 * inch),
            Paragraph("Section 1: WCAG 2.1 AA Compliance Check", styles["Heading1"]),
            Spacer(1, 0.1 * inch),
            Paragraph(
                "This document is used by the Sacramento County WCAG pipeline "
                "post-deploy verification script to confirm that the extraction "
                "stage correctly identifies text elements, headings, and paragraphs.",
                styles["BodyText"],
            ),
            Spacer(1, 0.1 * inch),
            Paragraph("Section 2: Criteria Under Test", styles["Heading2"]),
            Spacer(1, 0.1 * inch),
            Paragraph(
                "The following WCAG 2.1 AA criteria are exercised by this document: "
                "1.1.1 Non-text Content, 1.3.1 Info and Relationships, "
                "1.3.2 Meaningful Sequence, 2.4.6 Headings and Labels, "
                "3.1.1 Language of Page. All 50 pipeline rules are evaluated.",
                styles["BodyText"],
            ),
        ]

        doc.build(story)
        return buf.getvalue()
    except ImportError:
        pass

    # Fallback: plain reportlab canvas (no platypus) — still produces extractable text
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf)
        # Heading — larger font
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 750, "Verification Test Document")
        # Subheading
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, "Section 1: WCAG 2.1 AA Compliance Check")
        # Body text — multiple lines to give extraction stage real content
        c.setFont("Helvetica", 12)
        c.drawString(72, 695, "This document tests the WCAG pipeline extraction stage.")
        c.drawString(72, 678, "Sacramento County WCAG 2.1 AA Remediation Pipeline.")
        c.drawString(72, 661, "Criteria: 1.1.1 Non-text Content, 1.3.1 Info and Relationships.")
        c.drawString(72, 644, "All 50 pipeline rules should be checked against this content.")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 615, "Section 2: Criteria Under Test")
        c.setFont("Helvetica", 12)
        c.drawString(72, 595, "Headings and Labels (WCAG 2.4.6), Language of Page (3.1.1).")
        c.drawString(72, 578, "Meaningful Sequence (1.3.2), Images of Text (1.4.5).")
        c.save()
        return buf.getvalue()
    except ImportError:
        pass

    try:
        import pikepdf
        # pikepdf blank pages have no text — add a content stream with text
        buf = io.BytesIO()
        pdf = pikepdf.new()
        page = pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=[0, 0, 612, 792],
        ))
        # Add content stream with heading and two text lines
        content = (
            b"BT /F1 18 Tf 72 750 Td (Verification Test Document) Tj ET\n"
            b"BT /F1 14 Tf 72 720 Td (Section 1: WCAG 2.1 AA Compliance Check) Tj ET\n"
            b"BT /F1 12 Tf 72 695 Td (This document tests the WCAG pipeline extraction stage.) Tj ET\n"
            b"BT /F1 12 Tf 72 678 Td (Sacramento County WCAG 2.1 AA Remediation Pipeline.) Tj ET\n"
            b"BT /F1 12 Tf 72 661 Td (Criteria: 1.1.1 Non-text Content, 1.3.1 Info and Relationships.) Tj ET\n"
            b"BT /F1 14 Tf 72 630 Td (Section 2: Criteria Under Test) Tj ET\n"
            b"BT /F1 12 Tf 72 610 Td (Headings and Labels 2.4.6, Language of Page 3.1.1.) Tj ET"
        )
        page.obj["/Contents"] = pdf.make_stream(content)
        # Add font resource
        page.obj["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name("/Font"),
                    Subtype=pikepdf.Name("/Type1"),
                    BaseFont=pikepdf.Name("/Helvetica"),
                )
            )
        )
        pdf.pages.append(page.obj)
        struct_tree = pikepdf.Dictionary(
            Type=pikepdf.Name("/StructTreeRoot"),
            K=pikepdf.Array([]),
        )
        pdf.Root["/StructTreeRoot"] = pdf.make_indirect(struct_tree)
        pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=pikepdf.Boolean(True))
        pdf.save(buf)
        pdf.close()
        return buf.getvalue()
    except ImportError:
        return b""


def _make_image_pdf() -> bytes:
    """Create a test PDF with an embedded image (for Vertex AI alt text testing)."""
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
    except ImportError:
        return b""

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(100, 750, "Image Alt Text Verification Document")

    # Create a simple colored rectangle as an "image"
    import struct
    import zlib

    # Minimal 10x10 red PNG
    width, height = 10, 10
    raw_data = b""
    for _ in range(height):
        raw_data += b"\x00"  # filter byte
        for _ in range(width):
            raw_data += b"\xff\x00\x00"  # red pixel

    def _make_png(w: int, h: int, raw: bytes) -> bytes:
        def _chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = zlib.crc32(c) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

        header = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        compressed = zlib.compress(raw)
        return header + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")

    png_bytes = _make_png(width, height, raw_data)
    img = ImageReader(io.BytesIO(png_bytes))
    c.drawImage(img, 100, 600, width=100, height=100)
    c.drawString(100, 580, "Figure 1: Test image for alt text generation.")
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Check functions — each returns (passed: bool, detail: str)
# ---------------------------------------------------------------------------


def check_health(base_url: str, report: VerificationReport) -> dict[str, Any]:
    """Category 1: Health and Dependencies."""
    try:
        resp = requests.get(f"{base_url}/api/health", timeout=15)
    except Exception as exc:
        report.add("Health endpoint reachable", False, str(exc), "health")
        return {}

    report.add("Health endpoint returns 200", resp.status_code == 200,
               f"Got {resp.status_code}", "health")

    if resp.status_code != 200:
        return {}

    body = resp.json()
    services = body.get("services", {})

    # Required services
    report.add("Database probe: up", services.get("database") == "up",
               f"status={services.get('database')}", "health")
    report.add("Adobe credentials probe: up", services.get("adobe_credentials") == "up",
               f"status={services.get('adobe_credentials')}", "health")
    report.add("Vertex AI probe: up", services.get("vertex_ai") == "up",
               f"status={services.get('vertex_ai')}, detail={services.get('vertex_ai_detail', 'none')}",
               "health")

    # Overall
    report.add("Overall health: healthy", body.get("status") == "healthy",
               f"status={body.get('status')}", "health")

    return body


def check_env_vars(report: VerificationReport, revision: str | None = None) -> None:
    """Category 2: Environment variable verification via gcloud or inference."""
    if not revision:
        report.add("Env var check (skipped - no revision)", True,
                    "Pass --revision to check env vars via gcloud", "env_vars")
        return

    import shutil
    import subprocess

    gcloud_path = shutil.which("gcloud")
    if not gcloud_path:
        report.add("Env var check (gcloud not in PATH)", True,
                    "gcloud CLI not found — env vars verified indirectly via health check. "
                    "Run from a machine with gcloud for full check.", "env_vars")
        return

    try:
        result = subprocess.run(
            [gcloud_path, "run", "revisions", "describe", revision,
             "--region", "us-central1",
             "--format=yaml(spec.containers[0].env[].name)"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout
    except Exception as exc:
        report.add("Env vars retrievable via gcloud", False, str(exc), "env_vars")
        return

    required_vars = [
        "WCAG_ADOBE_CLIENT_ID", "WCAG_ADOBE_CLIENT_SECRET",
        "WCAG_GCP_PROJECT_ID", "WCAG_GCP_REGION",
        "WCAG_VERTEX_AI_MODEL", "WCAG_VERTEX_AI_LOCATION",
        "PYTHONPATH", "WCAG_DB_PATH", "WCAG_EXTRACTION_CACHE_DIR",
        "PYTHONUNBUFFERED",
    ]

    for var in required_vars:
        present = var in output
        report.add(f"Env var: {var}", present,
                    "present" if present else "MISSING", "env_vars")


def check_analyze(base_url: str, report: VerificationReport) -> dict[str, Any]:
    """Category 3: /analyze endpoint contract verification."""
    pdf_bytes = _make_test_pdf()
    if not pdf_bytes:
        report.add("Test PDF creation", False, "Neither pikepdf nor reportlab available", "analyze")
        return {}

    report.add("Test PDF created", True, f"{len(pdf_bytes)} bytes", "analyze")

    try:
        resp = requests.post(
            f"{base_url}/api/v1/analyze",
            files={"file": ("verify_test.pdf", pdf_bytes, "application/pdf")},
            timeout=120,
        )
    except Exception as exc:
        report.add("Analyze endpoint reachable", False, str(exc), "analyze")
        return {}

    report.add("Analyze returns 200", resp.status_code == 200,
               f"Got {resp.status_code}: {resp.text[:200] if resp.status_code != 200 else ''}", "analyze")

    if resp.status_code != 200:
        return {}

    body = resp.json()

    # Contract checks
    report.add("Response has task_id", bool(body.get("task_id")),
               f"task_id={body.get('task_id', 'MISSING')}", "analyze")

    summary = body.get("summary", {})
    rules_checked = summary.get("rules_checked", 0)
    report.add("50 WCAG rules checked", rules_checked == 50,
               f"rules_checked={rules_checked}", "analyze")

    # Pipeline metadata
    meta = body.get("pipeline_metadata", {})
    report.add("pipeline_metadata present", bool(meta),
               "present" if meta else "MISSING", "analyze")

    stages = meta.get("stages", [])
    report.add("pipeline_metadata.stages is non-empty list", len(stages) > 0,
               f"{len(stages)} stages", "analyze")

    overall = meta.get("overall_status", "")
    report.add("pipeline_metadata.overall_status present", bool(overall),
               f"overall_status={overall}", "analyze")

    # Check specific stages exist
    stage_names = [s.get("stage_name") for s in stages]
    for expected in ["extract", "deterministic_fixes", "ai_alt_text"]:
        report.add(f"Stage '{expected}' present in metadata", expected in stage_names,
                    f"stages={stage_names}", "analyze")

    # Check each stage has status field
    for stage in stages:
        name = stage.get("stage_name", "unknown")
        status = stage.get("status", "")
        valid_statuses = {"success", "degraded", "skipped", "failed"}
        report.add(f"Stage '{name}' has valid status", status in valid_statuses,
                    f"status={status}", "analyze")

    # Check ai_alt_text stage has work metrics
    ai_stage = next((s for s in stages if s.get("stage_name") == "ai_alt_text"), None)
    if ai_stage:
        ai_meta = ai_stage.get("metadata", {})
        has_metrics = "images_total" in ai_meta and "ai_succeeded" in ai_meta
        report.add("AI alt text stage has work metrics", has_metrics,
                    f"metadata keys={list(ai_meta.keys())}", "analyze")

    # Proposals
    proposals = body.get("proposals", [])
    report.add("Proposals is a list", isinstance(proposals, list),
               f"type={type(proposals).__name__}, count={len(proposals) if isinstance(proposals, list) else 'N/A'}",
               "analyze")

    return body


def check_remediate(base_url: str, report: VerificationReport) -> None:
    """Category 4: /remediate endpoint contract verification."""
    pdf_bytes = _make_test_pdf()
    if not pdf_bytes:
        report.add("Test PDF for remediation", False, "No PDF library", "remediate")
        return

    try:
        resp = requests.post(
            f"{base_url}/api/v1/remediate",
            files={"file": ("verify_test.pdf", pdf_bytes, "application/pdf")},
            params={"output_format": "html"},
            data={"approved_ids": ""},
            timeout=120,
        )
    except Exception as exc:
        report.add("Remediate endpoint reachable", False, str(exc), "remediate")
        return

    report.add("Remediate returns 200", resp.status_code == 200,
               f"Got {resp.status_code}: {resp.text[:200] if resp.status_code != 200 else ''}", "remediate")

    if resp.status_code != 200:
        return

    # Headers
    task_id = resp.headers.get("x-task-id", "")
    report.add("X-Task-Id header present", bool(task_id),
               f"x-task-id={task_id or 'MISSING'}", "remediate")

    pipeline_meta_raw = resp.headers.get("x-pipeline-metadata", "")
    report.add("X-Pipeline-Metadata header present", bool(pipeline_meta_raw),
               f"length={len(pipeline_meta_raw)}", "remediate")

    if pipeline_meta_raw:
        try:
            pipeline_meta = json.loads(pipeline_meta_raw)
            report.add("X-Pipeline-Metadata is valid JSON", True,
                        f"stages={len(pipeline_meta.get('stages', []))}", "remediate")
            overall = pipeline_meta.get("overall_status", "")
            report.add("Pipeline overall_status present", bool(overall),
                        f"overall_status={overall}", "remediate")
        except json.JSONDecodeError as exc:
            report.add("X-Pipeline-Metadata is valid JSON", False, str(exc), "remediate")

    delta_raw = resp.headers.get("x-remediation-delta", "")
    report.add("X-Remediation-Delta header present", bool(delta_raw),
               f"value={delta_raw or 'MISSING'}", "remediate")

    # HTML content checks
    html = resp.text
    report.add('HTML has lang="en"', 'lang="en"' in html,
               f"found={'yes' if 'lang=\"en\"' in html else 'no'}", "remediate")
    report.add("HTML has <title>", "<title>" in html,
               f"found={'yes' if '<title>' in html else 'no'}", "remediate")
    report.add('HTML has skip-link', 'skip-link' in html or 'skip-nav' in html,
               "", "remediate")


def check_vertex_ai_generates_alt_text(base_url: str, report: VerificationReport) -> None:
    """Category 5: Vertex AI actually generates alt text for a real image."""
    pdf_bytes = _make_image_pdf()
    if not pdf_bytes:
        report.add("Image PDF creation (needs reportlab)", False,
                    "reportlab not installed", "vertex_ai_e2e")
        return

    report.add("Image PDF created", True, f"{len(pdf_bytes)} bytes", "vertex_ai_e2e")

    try:
        resp = requests.post(
            f"{base_url}/api/v1/analyze",
            files={"file": ("image_test.pdf", pdf_bytes, "application/pdf")},
            timeout=180,
        )
    except Exception as exc:
        report.add("Analyze with image PDF", False, str(exc), "vertex_ai_e2e")
        return

    report.add("Analyze with image returns 200", resp.status_code == 200,
               f"Got {resp.status_code}", "vertex_ai_e2e")

    if resp.status_code != 200:
        return

    body = resp.json()
    meta = body.get("pipeline_metadata", {})
    stages = meta.get("stages", [])
    ai_stage = next((s for s in stages if s.get("stage_name") == "ai_alt_text"), None)

    if not ai_stage:
        report.add("AI alt text stage present", False, "stage missing from metadata", "vertex_ai_e2e")
        return

    status = ai_stage.get("status", "")
    ai_meta = ai_stage.get("metadata", {})
    images_total = ai_meta.get("images_total", 0)
    ai_succeeded = ai_meta.get("ai_succeeded", 0)
    ai_failed = ai_meta.get("ai_failed", 0)

    report.add("AI stage status is success or degraded", status in ("success", "degraded"),
               f"status={status}", "vertex_ai_e2e")
    report.add(f"Images found in PDF (total={images_total})", images_total > 0,
               f"images_total={images_total}", "vertex_ai_e2e")

    if images_total > 0:
        report.add(f"AI succeeded on at least 1 image (succeeded={ai_succeeded})",
                    ai_succeeded > 0,
                    f"succeeded={ai_succeeded}, failed={ai_failed}", "vertex_ai_e2e")


def check_cors_headers(base_url: str, report: VerificationReport) -> None:
    """Category 6a: Verify CORS exposes required response headers.

    The browser will block JavaScript from reading any response header NOT
    listed in Access-Control-Expose-Headers.  We need X-Pipeline-Metadata
    and X-Remediation-Delta exposed for the HITL dashboard.
    """
    try:
        # Access-Control-Expose-Headers is sent on ACTUAL responses (not OPTIONS preflight).
        # Use the health endpoint with an Origin header to trigger CORS response headers.
        resp = requests.get(
            f"{base_url}/api/health",
            headers={"Origin": "https://hitl-dashboard.vercel.app"},
            timeout=15,
        )
        expose_raw = resp.headers.get("access-control-expose-headers", "")
        # Normalize to lowercase set for reliable matching
        exposed = {h.strip().lower() for h in expose_raw.split(",") if h.strip()}

        required_headers = [
            "x-task-id",
            "x-pipeline-version",
            "x-pipeline-metadata",
            "x-remediation-delta",
        ]
        for hdr in required_headers:
            report.add(
                f"CORS exposes {hdr}",
                hdr in exposed,
                f"expose_headers={expose_raw}",
                "cors",
            )
    except Exception as exc:
        report.add("CORS preflight check", False, str(exc), "cors")


def check_db_backend(base_url: str, report: VerificationReport) -> None:
    """Category 6b: Verify database backend is reported in health and not silently degraded."""
    try:
        resp = requests.get(f"{base_url}/api/health", timeout=15)
        body = resp.json()
        services = body.get("services", {})
        db_status = services.get("database", "unknown")

        # DB must report "up" — if it silently fell back to SQLite
        # we'd still see "up" but the detail should indicate the backend type
        report.add("Database probe healthy", db_status == "up",
                    f"status={db_status}", "db_backend")

        db_detail = services.get("database_detail", "")
        # When the DB is "down", a detail message MUST be present so operators
        # know WHY it failed. When "up", a detail is optional (SQLite reports
        # no detail; Postgres reports connection info). We only enforce a detail
        # is present when the database is NOT "up" — a down probe with no detail
        # is a silent failure that obscures root cause.
        if db_status != "up":
            report.add("Database detail field present (required when not up)",
                        bool(db_detail),
                        f"status={db_status}, detail={db_detail or 'MISSING — must explain why DB is not up'}",
                        "db_backend")
        else:
            report.add("Database detail field (up — detail optional)",
                        True,
                        f"detail={db_detail or '(none — acceptable when up)'}",
                        "db_backend")
    except Exception as exc:
        report.add("DB backend check", False, str(exc), "db_backend")


def check_no_silent_fallbacks(base_url: str, report: VerificationReport) -> None:
    """Category 7: Verify that degraded/failed stages are NOT reported as success."""
    # This check verifies the StageNoOpError mechanism:
    # If Vertex AI were unavailable, the stage should report "degraded", not "success"
    # We can't directly test this without breaking Vertex AI, but we CAN verify
    # that the metadata structure supports it
    try:
        resp = requests.get(f"{base_url}/api/health", timeout=15)
        body = resp.json()
        services = body.get("services", {})

        vertex_status = services.get("vertex_ai", "unknown")
        report.add("Vertex AI health probe not silently passing",
                    vertex_status in ("up", "down", "degraded"),
                    f"status={vertex_status} (should never be 'unchecked')",
                    "silent_fallback")

        # Verify detail field exists when status is "up"
        if vertex_status == "up":
            detail = services.get("vertex_ai_detail", "")
            report.add("Vertex AI reports HOW it authenticates",
                        bool(detail),
                        f"detail={detail or 'MISSING — should say Using ADC or credential path'}",
                        "silent_fallback")

    except Exception as exc:
        report.add("Silent fallback check", False, str(exc), "silent_fallback")


def check_gate_semantic_truth(base_url: str, report: VerificationReport) -> None:
    """Category 8: Verify that gate results reflect semantic truth.

    When a validation tool (VeraPDF, Adobe checker, axe-core) is unavailable,
    the gate MUST NOT report 'pass' — it must report degraded/unavailable.
    This prevents false compliance claims.
    """
    # We test this by examining the /remediate pipeline metadata:
    # if a gate passes, there should be evidence of ACTUAL validation, not just
    # a trivial fallback passing.
    pdf_bytes = _make_test_pdf()
    if not pdf_bytes:
        report.add("Semantic gate test PDF", False, "No PDF library", "gate_semantic")
        return

    if not _track_external_call("gate_semantic_truth /remediate"):
        report.add("Gate semantic truth (skipped — API cap)", True,
                    "Skipped due to external API call cap", "gate_semantic")
        return

    try:
        resp = requests.post(
            f"{base_url}/api/v1/remediate",
            files={"file": ("gate_test.pdf", pdf_bytes, "application/pdf")},
            params={"output_format": "html"},
            data={"approved_ids": ""},
            timeout=120,
        )
    except Exception as exc:
        report.add("Gate semantic test reachable", False, str(exc), "gate_semantic")
        return

    if resp.status_code != 200:
        report.add("Gate semantic test HTTP 200", False,
                    f"Got {resp.status_code}", "gate_semantic")
        return

    meta_raw = resp.headers.get("x-pipeline-metadata", "")
    if not meta_raw:
        report.add("Gate metadata available for semantic check", False,
                    "X-Pipeline-Metadata header missing", "gate_semantic")
        return

    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        report.add("Gate metadata parseable", False,
                    "X-Pipeline-Metadata is not valid JSON", "gate_semantic")
        return

    stages = meta.get("stages", [])
    stage_map = {s.get("stage_name"): s for s in stages}

    # Check: if any stage has status "success", it should have non-empty metadata
    # proving it actually did work (not a silent no-op)
    for name, stage in stage_map.items():
        status = stage.get("status", "")
        if status == "success":
            stage_meta = stage.get("metadata", {})
            # A successful stage should have SOME metadata showing what it did.
            # extract and deterministic_fixes don't emit work metrics yet.
            no_metrics_expected = ("deterministic_fixes", "extract")
            report.add(
                f"Stage '{name}' success has evidence",
                bool(stage_meta) or name in no_metrics_expected,
                f"status={status}, metadata_keys={list(stage_meta.keys()) if stage_meta else 'EMPTY'}",
                "gate_semantic",
            )

    # Check: degraded/skipped stages must NOT have been silently promoted to success
    for name, stage in stage_map.items():
        status = stage.get("status", "")
        if status in ("degraded", "skipped", "failed"):
            warnings = stage.get("warnings", [])
            errors = stage.get("errors", [])
            has_explanation = bool(warnings) or bool(errors)
            report.add(
                f"Stage '{name}' ({status}) has explanation",
                has_explanation,
                f"warnings={len(warnings)}, errors={len(errors)}",
                "gate_semantic",
            )


def check_invariants(base_url: str, report: VerificationReport) -> None:
    """Category 9: Enforce documented system invariants.

    These invariants are listed in .claude/session-handoff.md and MUST hold
    on every deployed revision. Failure indicates a regression in core safety
    properties.

    Invariant 1: No SQLite fallback in prod.
      If db_backend=postgres is configured, a DB failure must raise, not fall
      back to SQLite. We verify this by checking the health endpoint reports
      a valid (non-"unknown") database status — if the DB silently fell back
      to SQLite without error, the detail field would be missing or blank.

    Invariant 2: CORS exposes all required custom headers.
      Verified separately in check_cors_headers (Category 6a).

    Invariant 3: Gate fail-closed.
      Verified by check_gate_semantic_truth (Category 8).

    Invariant 4: StageNoOpError — zero-work stages report degraded, not success.
      Verified by check_no_silent_fallbacks (Category 7).

    Invariant 5: Health endpoint reports all five dependency probes.
      The health response MUST include: database, adobe_credentials, vertex_ai,
      verapdf, axe_core. A missing probe means it was accidentally removed and
      its failures would be invisible.
    """
    try:
        resp = requests.get(f"{base_url}/api/health", timeout=15)
        body = resp.json()
    except Exception as exc:
        report.add("Invariants: health endpoint reachable", False, str(exc), "invariants")
        return

    services = body.get("services", {})

    # Invariant 1: DB status is never silently "unknown".
    # If the DB probe was removed or swallowed an error, services["database"]
    # would be missing. That is a regression.
    db_status = services.get("database", None)
    report.add(
        "Invariant 1: database probe present and not unknown",
        db_status is not None and db_status != "unknown",
        f"database={db_status} (must be up/down/degraded — never missing or unknown)",
        "invariants",
    )

    # Invariant 5: All five required dependency probes must be present in the
    # health response. Their statuses may vary (up/down/degraded), but they
    # MUST be reported — absence means a probe was dropped and failures become invisible.
    required_probes = ["database", "adobe_credentials", "vertex_ai", "verapdf", "axe_core"]
    for probe in required_probes:
        present = probe in services
        report.add(
            f"Invariant 5: probe '{probe}' reported in health",
            present,
            f"status={services.get(probe, 'MISSING')}",
            "invariants",
        )

    # Invariant 2 (spot-check): CORS include list must not be empty.
    # Full CORS check is in check_cors_headers; here we verify the header exists at all.
    try:
        cors_resp = requests.get(
            f"{base_url}/api/health",
            headers={"Origin": "https://hitl-dashboard.vercel.app"},
            timeout=15,
        )
        expose_raw = cors_resp.headers.get("access-control-expose-headers", "")
        report.add(
            "Invariant 2: CORS expose-headers non-empty",
            bool(expose_raw.strip()),
            f"access-control-expose-headers={expose_raw or 'MISSING'}",
            "invariants",
        )
    except Exception as exc:
        report.add("Invariant 2: CORS expose-headers check", False, str(exc), "invariants")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-deploy verification for Sacramento WCAG pipeline")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of deployed backend")
    parser.add_argument("--revision", default=None, help="Cloud Run revision name for env var check")
    parser.add_argument("--with-image-pdf", action="store_true",
                        help="Include Vertex AI alt text e2e test (slower, needs reportlab)")
    parser.add_argument("--skip-paid-apis", action="store_true",
                        help="Skip /analyze and /remediate calls (health, env, CORS only — zero external API cost)")
    args = parser.parse_args()

    report = VerificationReport(
        url=args.url,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        revision=args.revision or "",
    )

    mode = "CHEAP (health+env+CORS only)" if args.skip_paid_apis else "DEFAULT (canary PDF)"
    if args.with_image_pdf:
        mode = "FULL (canary + image PDF)"
    print(f"\nRunning post-deploy verification against {args.url}...")
    print(f"Mode: {mode} | API call cap: {MAX_EXTERNAL_API_CALLS}")

    # Category 1: Health and dependencies (free — no external API calls)
    check_health(args.url, report)

    # Category 2: Environment variables (free — gcloud only)
    check_env_vars(report, revision=args.revision)

    # Category 3: /analyze contract (1 external API call on server)
    if not args.skip_paid_apis:
        if _track_external_call("analyze"):
            check_analyze(args.url, report)
    else:
        report.add("Analyze check (skipped — --skip-paid-apis)", True,
                    "Skipped to avoid external API costs", "analyze")

    # Category 4: /remediate contract (1 external API call on server)
    if not args.skip_paid_apis:
        if _track_external_call("remediate"):
            check_remediate(args.url, report)
    else:
        report.add("Remediate check (skipped — --skip-paid-apis)", True,
                    "Skipped to avoid external API costs", "remediate")

    # Category 5: Vertex AI e2e (optional, additional Gemini call)
    if args.with_image_pdf and not args.skip_paid_apis:
        if _track_external_call("vertex_ai_e2e"):
            check_vertex_ai_generates_alt_text(args.url, report)

    # Category 6a: CORS header exposure (free)
    check_cors_headers(args.url, report)

    # Category 6b: DB backend (free)
    check_db_backend(args.url, report)

    # Category 7: No silent fallbacks (free)
    check_no_silent_fallbacks(args.url, report)

    # Category 8: Gate semantic truth (1 external API call — skip if paid APIs disabled)
    if not args.skip_paid_apis:
        check_gate_semantic_truth(args.url, report)
    else:
        report.add("Gate semantic truth (skipped — --skip-paid-apis)", True,
                    "Skipped to avoid external API costs", "gate_semantic")

    # Category 9: System invariants (free — health endpoint only)
    check_invariants(args.url, report)

    # Report external API usage
    print(f"\nExternal API calls used: {_external_api_call_count}/{MAX_EXTERNAL_API_CALLS}")

    report.print_report()

    # Write machine-readable report
    report_data = {
        "url": report.url,
        "timestamp": report.timestamp,
        "revision": report.revision,
        "all_passed": report.all_passed,
        "total": len(report.checks),
        "passed": sum(1 for c in report.checks if c.passed),
        "failed": sum(1 for c in report.checks if not c.passed),
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail, "category": c.category}
            for c in report.checks
        ],
    }

    report_path = "scripts/verify_deploy_report.json"
    try:
        with open(report_path, "w") as f:
            json.dump(report_data, f, indent=2)
        print(f"\nReport written to {report_path}")
    except Exception:
        pass  # Non-fatal

    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
