"""Synchronous PDF-to-accessible-HTML/PDF converter.

Orchestrates the entire remediation pipeline in a single synchronous call:
  Adobe Extract → reconstruct document structure → PDFUABuilder → output

Designed for local testing without Pub/Sub, GCS, or inter-service HTTP calls.

Key design: Reconstructs the FULL document structure from Adobe Extract's
element-by-element JSON, including proper table reconstruction with headers
and data rows. Adobe returns elements with paths like:
    //Document/Sect/Table/TR[6]/TH/P     → text in a header cell
    //Document/Sect/Table/TR[7]/TD[2]/P  → text in a data cell
Text is always in the leaf /P child, not on the cell container.

For multi-page tables (like Sacramento County fee schedules where each page
is a different zone), the table is split by page to produce separate
accessible tables per zone/page.

## Pipeline Stages (IR-based)

    stage_extract(pdf_bytes, filename) -> IRDocument
    stage_build_html(ir_doc, title)    -> (html_str, PDFUABuilder)
    stage_validate(builder, html)      -> dict
    stage_output(html, format, builder)-> (bytes, content_type)

convert_pdf_sync() orchestrates these stages. The public API is unchanged.
"""

from __future__ import annotations

# Load .env before any SDK imports pick up credentials
from dotenv import load_dotenv
load_dotenv(override=True)

import base64
import logging
import os
import re
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from services.common.config import settings
from services.common.remediation_events import RemediationComponent, RemediationEventCollector
from services.common.ir import (
    BlockSource,
    BlockType,
    IRBlock,
    IRDocument,
    IRPage,
    RemediationStatus,
    ValidationMode,
    dedupe_tables_in_page,
)
from services.common.database import get_db
from services.common.gates import run_gate_g3
from services.common.telemetry import get_tracer
from services.common.telemetry_collector import TelemetryCollector
from services.common.verapdf_client import VeraPDFClient, VeraPDFResult
from services.recompilation.pdfua_builder import PDFUABuilder

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

# Lazy import to avoid circular dependency at module load time
def _cache_remediation_events(task_id: str, events: list[dict]) -> None:
    try:
        from services.ingestion.api_fixes import cache_events
        cache_events(task_id, events)
    except Exception:
        pass


class ValidationBlockedError(Exception):
    """Raised when validation gates detect critical issues that block delivery.

    The pipeline MUST NOT serve output to users when this is raised.
    The router should convert this to HTTP 422 with violation details.
    """

    def __init__(self, message: str, violations: list[dict] | None = None):
        super().__init__(message)
        self.violations = violations or []

OutputFormat = Literal["html", "pdf"]


# ---------------------------------------------------------------------------
# Public staged API
# ---------------------------------------------------------------------------


def stage_extract(pdf_bytes: bytes, filename: str) -> IRDocument:
    """Stage 1+2: Extract structure from PDF and convert to IR.

    Calls Adobe Extract (or pypdf fallback), then runs _reconstruct_document()
    to build the legacy element list, and wraps as an IRDocument.
    """
    document_id = str(uuid.uuid4())

    with tracer.start_as_current_span("stage.extract") as span:
        # Keep a persistent tmp dir reference so pdf_path stays valid
        # for _reconstruct_document's PyMuPDF extraction call.
        _tmp = tempfile.TemporaryDirectory()
        try:
            local_pdf = Path(_tmp.name) / filename
            local_pdf.write_bytes(pdf_bytes)
            extract_json = _run_extraction(local_pdf)

            raw_count = len(extract_json.get("elements", []))
            span.set_attribute("extraction.raw_elements", raw_count)

            elements = _reconstruct_document(extract_json, pdf_path=local_pdf)
            span.set_attribute("reconstruction.output_elements", len(elements))

            # Phase 5B: Extract form fields from the PDF via pikepdf
            # (Adobe Extract JSON doesn't enumerate AcroForm widget annotations)
            form_fields = _extract_form_fields(local_pdf)
            if form_fields:
                elements.extend(form_fields)
                span.set_attribute("extraction.form_fields", len(form_fields))
        finally:
            _tmp.cleanup()

        ir_doc = _elements_to_ir(elements, extract_json, document_id, filename)
        span.set_attribute("ir.pages", ir_doc.page_count)
        span.set_attribute("ir.blocks", len(ir_doc.all_blocks()))

        # Persist image bytes to SQLite for HITL preview (non-blocking)
        images_stored = _persist_image_assets(ir_doc)
        span.set_attribute("ir.images_stored", images_stored)
        logger.info(
            "stage_extract: %d raw → %d elements → IR(%d pages, %d blocks) — %d images persisted",
            raw_count, len(elements), ir_doc.page_count, len(ir_doc.all_blocks()),
            images_stored,
        )
        return ir_doc


# ---------------------------------------------------------------------------
# AI alt text stage
# ---------------------------------------------------------------------------

_GENERIC_ALT_PATTERN = re.compile(
    r"^\[Figure on page .+ — alt text requires review\]$"
)


def _vertex_ai_available() -> bool:
    """Return True when Vertex AI credentials and model config are both present."""
    return bool(settings.vertex_ai_model) and bool(
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )


# Patterns for complex image classification (W3C alt decision tree)
_RE_COMPLEX_IMAGE = re.compile(
    r"\b(?:chart|graph|map|diagram|figure\s*\d+)\b", re.IGNORECASE
)
_RE_FIGURE_REF = re.compile(
    r"\b(?:see\s+figure|as\s+shown\s+in|figure\s+\d+|fig\.\s*\d+)\b", re.IGNORECASE
)


def _classify_image_w3c(
    block: IRBlock,
    all_blocks: list[IRBlock],
    idx: int,
) -> str:
    """Classify an image as decorative, informative, or complex per W3C alt decision tree.

    Uses heuristics based on IRBlock data (no raw figure element bounding boxes):

    - **Decorative**: alt text / content is empty AND no src data (no actual image bytes),
      indicating a spacer or layout artifact.
    - **Complex**: surrounding text contains words like "chart", "graph", "diagram",
      "table", "map", or "figure N" references — indicates the image conveys complex
      data that needs extended description.
    - **Informative**: everything else (default) — a meaningful image that needs
      concise descriptive alt text.

    Args:
        block: The IMAGE IRBlock being classified.
        all_blocks: All blocks in the document (for context window).
        idx: Index of the block in all_blocks.

    Returns:
        One of "decorative", "informative", or "complex".
    """
    attrs = block.attributes
    alt = attrs.get("alt", "")
    src = attrs.get("src", "")

    # Decorative: no alt text AND no image data — likely a spacer/artifact
    if not alt and not src:
        return "decorative"

    # Gather surrounding text for context analysis
    surrounding_parts: list[str] = []
    window = 5
    for offset in range(max(0, idx - window), min(len(all_blocks), idx + window + 1)):
        if offset == idx:
            continue
        b = all_blocks[offset]
        if b.block_type in (BlockType.PARAGRAPH, BlockType.HEADING):
            text = b.content.strip()
            if text:
                surrounding_parts.append(text)

    surrounding = " ".join(surrounding_parts)

    # Complex: surrounding text references charts, graphs, diagrams, figures
    complex_matches = len(_RE_COMPLEX_IMAGE.findall(surrounding))
    ref_matches = len(_RE_FIGURE_REF.findall(surrounding))
    if complex_matches > 0 or ref_matches >= 2:
        return "complex"

    return "informative"


def stage_ai_alt_text(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> IRDocument:
    """Stage 2b: Enrich IMAGE blocks with AI-generated WCAG 1.1.1-compliant alt text.

    For each IMAGE block that:
    - Still has a generic placeholder alt text (the pattern set during extraction)
    - Has a base64 ``src`` attribute (i.e. image bytes were successfully extracted)

    ...this stage calls Vertex AI (Gemini multimodal) to generate a descriptive alt
    text string. The block's ``alt`` attribute is updated in-place on the IRDocument.

    Conditions that cause the stage to skip silently:
    - ``settings.vertex_ai_model`` is not set
    - ``GOOGLE_APPLICATION_CREDENTIALS`` env var is absent
    - The image block has no ``src`` (image bytes were not available)
    - The alt text is already non-generic (Adobe provided real alt text)

    All eligible images are processed. Images are sent to Gemini in batches of 5
    with a 2-second delay between batches to avoid Gemini 429 rate limit errors.
    Progress is logged every 10 images processed.

    Args:
        ir_doc: The IRDocument produced by ``stage_extract``.
        collector: Optional remediation event collector for audit trail.

    Returns:
        The same ``ir_doc`` object with updated ``attributes["alt"]`` on eligible
        IMAGE blocks. Always returns a valid IRDocument — never raises.
    """
    if not _vertex_ai_available():
        logger.debug(
            "stage_ai_alt_text: Vertex AI not configured — skipping alt text generation"
        )
        return ir_doc

    # Lazy import — avoids hard dependency when vertexai SDK is not installed
    try:
        from services.ai_drafting.vertex_client import generate_alt_text_for_image
    except ImportError as exc:
        logger.warning(
            "stage_ai_alt_text: could not import vertex_client (%s) — skipping", exc
        )
        return ir_doc

    all_blocks = ir_doc.all_blocks()
    images_total = sum(1 for b in all_blocks if b.block_type == BlockType.IMAGE)
    processed = 0
    ai_succeeded = 0
    ai_failed = 0
    skipped_no_src = 0
    skipped_has_alt = 0

    # Collect eligible (block, idx) pairs first so we can batch with rate limiting
    eligible: list[tuple[int, IRBlock]] = []
    for idx, block in enumerate(all_blocks):
        if block.block_type != BlockType.IMAGE:
            continue

        attrs = block.attributes
        alt = attrs.get("alt", "")
        src = attrs.get("src", "")

        # Skip if Adobe already provided real alt text (not a generic placeholder)
        if alt and not _GENERIC_ALT_PATTERN.match(alt):
            skipped_has_alt += 1
            continue

        # W3C alt-text decision tree classification
        img_class = _classify_image_w3c(block, all_blocks, idx)

        if img_class == "decorative":
            # Decorative: set empty alt, mark as aria-hidden, skip AI call
            block.attributes["alt"] = ""
            block.attributes["aria-hidden"] = "true"
            block.remediation_status = RemediationStatus.AI_DRAFTED
            logger.debug(
                "stage_ai_alt_text: block %d classified as decorative — skipping AI",
                idx,
            )
            continue

        if img_class == "complex":
            # Flag for HITL dashboard to pick up
            block.attributes["data-complexity"] = "complex"

        # Skip if no image data was extracted (no src → can't send image to Gemini)
        if not src or not src.startswith("data:"):
            skipped_no_src += 1
            continue

        eligible.append((idx, block))

    total_eligible = len(eligible)

    with tracer.start_as_current_span("stage.ai_alt_text") as span:
        _BATCH_SIZE = 5
        for batch_start in range(0, total_eligible, _BATCH_SIZE):
            batch = eligible[batch_start:batch_start + _BATCH_SIZE]

            # Rate limiting: pause between batches (not before the first batch)
            if batch_start > 0:
                time.sleep(2.0)

            for idx, block in batch:
                attrs = block.attributes
                alt = attrs.get("alt", "")
                src = attrs.get("src", "")

                # Extract base64 payload and MIME type from the data URI.
                # Format: "data:<mime>;base64,<b64data>"
                try:
                    header, b64_payload = src.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                except (ValueError, IndexError):
                    logger.debug(
                        "stage_ai_alt_text: malformed data URI on block index %d — skipping",
                        idx,
                    )
                    skipped_no_src += 1
                    continue

                img_class = block.attributes.get("data-complexity", "informative")

                # Gather surrounding text context from adjacent non-image blocks
                surrounding = _get_surrounding_text(all_blocks, idx, window=3)

                # Call Vertex AI — falls back to fallback_alt on any error
                new_alt = generate_alt_text_for_image(
                    image_base64=b64_payload,
                    image_mime=mime,
                    surrounding_text=surrounding,
                    page_num=block.page_num + 1,  # Convert 0-based IR to 1-based for prompt
                    fallback_alt=alt,
                )

                # For complex images, append a review note to the AI-generated alt text
                if img_class == "complex" and new_alt and new_alt != alt:
                    new_alt = new_alt.rstrip(".") + ". (Complex image — requires human review)"

                # Update the block's alt text and remediation status
                block.attributes["alt"] = new_alt
                if new_alt != alt:
                    block.remediation_status = RemediationStatus.AI_DRAFTED
                    ai_succeeded += 1
                    if collector:
                        collector.record(
                            RemediationComponent.ALT_TEXT,
                            element_id=block.block_id,
                            before=alt if alt else None,
                            after=new_alt,
                            source="ai",
                        )
                else:
                    # Gemini returned the fallback (unchanged) — likely an error or timeout
                    ai_failed += 1
                    block.attributes["data-needs-review"] = "alt-text"

                processed += 1

                # Progress logging every 10 images
                if processed % 10 == 0:
                    logger.info(
                        "AI alt text progress: %d/%d processed",
                        processed,
                        total_eligible,
                    )

        span.set_attribute("ai_alt_text.images_total", images_total)
        span.set_attribute("ai_alt_text.ai_attempted", processed)
        span.set_attribute("ai_alt_text.ai_succeeded", ai_succeeded)
        span.set_attribute("ai_alt_text.ai_failed", ai_failed)
        span.set_attribute("ai_alt_text.skipped_no_src", skipped_no_src)
        span.set_attribute("ai_alt_text.skipped_has_alt", skipped_has_alt)

        placeholder_remaining = images_total - skipped_has_alt - ai_succeeded

        # Structured telemetry summary (visible in Cloud Run logs)
        logger.info(
            "stage_ai_alt_text SUMMARY: images_total=%d ai_attempted=%d "
            "ai_succeeded=%d ai_failed=%d skipped_no_src=%d "
            "skipped_has_alt=%d placeholder_remaining=%d",
            images_total,
            processed,
            ai_succeeded,
            ai_failed,
            skipped_no_src,
            skipped_has_alt,
            placeholder_remaining,
        )

    return ir_doc


def _get_surrounding_text(
    blocks: list[IRBlock],
    image_idx: int,
    window: int = 3,
) -> str:
    """Collect text from up to ``window`` non-image blocks before and after ``image_idx``.

    Args:
        blocks: Flat list of all IRBlocks in reading order.
        image_idx: Index of the IMAGE block we are generating alt text for.
        window: Maximum number of adjacent blocks to inspect in each direction.

    Returns:
        Concatenated text string from adjacent blocks, separated by a space.
        Returns an empty string if no surrounding text blocks are found.
    """
    text_parts: list[str] = []

    # Look backward (before the image)
    before_count = 0
    for i in range(image_idx - 1, max(-1, image_idx - window * 2 - 1), -1):
        if before_count >= window:
            break
        b = blocks[i]
        if b.block_type in (BlockType.PARAGRAPH, BlockType.HEADING):
            text = b.content.strip()
            if text:
                text_parts.insert(0, text)
                before_count += 1

    # Look forward (after the image)
    after_count = 0
    for i in range(image_idx + 1, min(len(blocks), image_idx + window * 2 + 1)):
        if after_count >= window:
            break
        b = blocks[i]
        if b.block_type in (BlockType.PARAGRAPH, BlockType.HEADING):
            text = b.content.strip()
            if text:
                text_parts.append(text)
                after_count += 1

    return " ".join(text_parts)


# ---------------------------------------------------------------------------
# Image asset persistence helpers
# ---------------------------------------------------------------------------


def _parse_data_uri(data_uri: str) -> tuple[bytes, str] | None:
    """Parse a data URI into (raw_bytes, mime_type). Returns None on failure."""
    m = re.match(r"data:([^;]+);base64,(.+)", data_uri, re.DOTALL)
    if not m:
        return None
    mime = m.group(1)
    try:
        raw = base64.b64decode(m.group(2))
        return raw, mime
    except Exception:
        return None


def _persist_image_assets(ir_doc: IRDocument) -> int:
    """Store image bytes from IR blocks to SQLite for HITL preview.

    Iterates all IMAGE blocks in the IRDocument. For each block with a
    ``data:`` URI src, extracts raw bytes, generates a deterministic
    ``image_id`` = ``img_p{page_num}_i{figure_idx}`` (counter per page),
    stores to SQLite via ``insert_image_asset``, and sets
    ``block.attributes["image_id"]`` so the IR carries the reference forward.

    Returns:
        Number of image assets successfully stored.
    """
    db = get_db(settings.db_path)
    stored = 0
    page_figure_counters: dict[int, int] = {}

    for block in ir_doc.all_blocks():
        if block.block_type != BlockType.IMAGE:
            continue
        src = block.attributes.get("src", "")
        if not src.startswith("data:"):
            continue
        parsed = _parse_data_uri(src)
        if parsed is None:
            continue
        raw_bytes, mime = parsed

        page_num = block.page_num
        idx = page_figure_counters.get(page_num, 0)
        page_figure_counters[page_num] = idx + 1

        image_id = f"img_p{page_num}_i{idx}"
        block.attributes["image_id"] = image_id

        try:
            db.insert_image_asset(
                image_id=image_id,
                document_id=ir_doc.document_id,
                page_num=page_num,
                mime_type=mime,
                image_data=raw_bytes,
            )
            stored += 1
        except Exception:
            logger.warning("Failed to persist image %s", image_id, exc_info=True)

    return stored


def stage_build_html(
    ir_doc: IRDocument,
    title: str,
    collector: RemediationEventCollector | None = None,
) -> tuple[str, PDFUABuilder]:
    """Stage 3: Convert IR to semantic HTML via PDFUABuilder."""
    with tracer.start_as_current_span("stage.html_build") as span:
        builder = PDFUABuilder(
            document_id=ir_doc.document_id,
            document_title=title,
        )
        for elem in ir_doc.to_legacy_elements():
            builder.add_element(
                element_type=elem["type"],
                content=elem["content"],
                attributes=elem.get("attributes", {}),
            )
        html_content = builder.build_semantic_html()
        span.set_attribute("html.size_bytes", len(html_content.encode("utf-8")))
        return html_content, builder


def stage_validate(
    builder: PDFUABuilder,
    html_content: str,
    mode: ValidationMode = ValidationMode.PUBLISH,
) -> dict[str, Any]:
    """Stage 4: Accessibility validation with critical-violation blocking.

    Runs ``PDFUABuilder.validate_accessibility()`` and augments the result with
    a ``"blocked"`` flag when any CRITICAL violations are present.  The pipeline
    is NOT interrupted — HTML generation continues regardless — but the flag is
    visible in the injected banner and in the HTML comment metadata so that
    downstream consumers (router, HITL dashboard) can surface the issue.

    Blocking rules (implemented in ``validate_accessibility``):
      CRITICAL (always block):
        - Missing ``lang`` attribute on ``<html>`` (WCAG 3.1.1)
        - Missing or empty ``<title>`` element (WCAG 2.4.2)
      SERIOUS (block when threshold exceeded):
        - More than 50% of images missing alt text (WCAG 1.1.1)
        - Any ``<table>`` without ``<th scope>`` headers (WCAG 1.3.1)
      WARNING (annotation only, never blocks):
        - Heading hierarchy skips (WCAG 2.4.6)

    Args:
        builder: PDFUABuilder instance used to validate the HTML.
        html_content: Semantic HTML string to validate.
        mode: ValidationMode passed through to ``validate_accessibility()``.
              DRAFT mode relaxes blocking thresholds.
    """
    with tracer.start_as_current_span("stage.validation") as span:
        validation = builder.validate_accessibility(html_content, mode=mode)
        score = validation.get("score", 0)
        violations = validation.get("violations", [])
        blocked = validation.get("blocked", False)
        critical_violations = validation.get("critical_violations", [])
        serious_violations = validation.get("serious_violations", [])

        span.set_attribute("validation.score", score)
        span.set_attribute("validation.violations", len(violations))
        span.set_attribute("validation.blocked", blocked)
        span.set_attribute("validation.critical_count", len(critical_violations))
        span.set_attribute("validation.serious_count", len(serious_violations))

        if blocked:
            logger.error(
                "Validation BLOCKED: document_id=%s score=%.2f "
                "critical=%s serious=%s",
                builder.document_id,
                score,
                critical_violations,
                serious_violations,
            )
        else:
            logger.info(
                "Validation: score=%.2f violations=%d blocked=False",
                score,
                len(violations),
            )

        return validation


def stage_output(
    html_content: str,
    output_format: OutputFormat,
    builder: PDFUABuilder,
    *,
    pdf_bytes: bytes | None = None,
    ir_doc: IRDocument | None = None,
    collector: RemediationEventCollector | None = None,
) -> tuple[bytes, str]:
    """Stage 5: Output generation (HTML bytes or tagged PDF bytes).

    For ``output_format="pdf"``, prefers the Adobe Auto-Tag → pikepdf enhance
    path which preserves the original visual layout with proper PDF/UA tags.
    Falls back to reportlab rendering when Auto-Tag is unavailable.

    Args:
        html_content: Semantic HTML from ``stage_build_html()``.
        output_format: ``"html"`` or ``"pdf"``.
        builder: PDFUABuilder (used as reportlab fallback for PDF).
        pdf_bytes: Original source PDF bytes (required for Auto-Tag path).
        ir_doc: IRDocument with AI alt text and headings (for tag enhancement).
        collector: Optional remediation event collector for audit trail.
    """
    with tracer.start_as_current_span("stage.output_generation") as span:
        if output_format == "pdf":
            output_bytes = _generate_tagged_pdf(
                pdf_bytes=pdf_bytes,
                ir_doc=ir_doc,
                html_content=html_content,
                builder=builder,
                span=span,
                collector=collector,
            )
            content_type = "application/pdf"
        else:
            output_bytes = html_content.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        span.set_attribute("output.size_bytes", len(output_bytes))
        span.set_attribute("output.content_type", content_type)
        return output_bytes, content_type


def _generate_tagged_pdf(
    pdf_bytes: bytes | None,
    ir_doc: IRDocument | None,
    html_content: str,
    builder: PDFUABuilder,
    span: Any,
    collector: RemediationEventCollector | None = None,
) -> bytes:
    """Generate a tagged PDF/UA document.

    Strategy (ordered by fidelity):
      1. Try Adobe Auto-Tag → pikepdf enhance (preserves original layout + tags)
      2. Fail with a clear error — Playwright/Chromium print-to-PDF does NOT
         produce tagged PDFs (no /StructTreeRoot, no heading tags, no alt text
         in the PDF structure). Shipping an untagged PDF as "remediated" would
         be worse than the original.
    """
    # Strategy 1: Auto-Tag path when source PDF bytes are available
    if pdf_bytes and ir_doc:
        tagged = _try_auto_tag_path(pdf_bytes, ir_doc, span, collector=collector)
        if tagged:
            return tagged

    # Auto-Tag unavailable — fail with a clear explanation.
    # Playwright/Chromium print-to-PDF does NOT produce tagged PDFs.
    # The output would have zero accessibility structure, making it worse
    # than the original. We must not ship an untagged PDF as "remediated".
    span.set_attribute("output.pdf_method", "blocked_no_auto_tag")
    logger.error(
        "_generate_tagged_pdf: Adobe Auto-Tag unavailable. Cannot produce "
        "accessible PDF output without it. Playwright/Chromium print-to-PDF "
        "does NOT generate tagged PDFs (no /StructTreeRoot, no /Figure tags, "
        "no /Alt attributes). Returning HTML output is still available."
    )
    raise RuntimeError(
        "PDF output requires Adobe Auto-Tag for accessibility compliance. "
        "Auto-Tag is currently unavailable (check Adobe credentials). "
        "The pipeline can still produce accessible HTML output — "
        "select HTML format instead, or configure Adobe API credentials."
    )


def _try_auto_tag_path(
    pdf_bytes: bytes,
    ir_doc: IRDocument,
    span: Any,
    collector: RemediationEventCollector | None = None,
) -> bytes | None:
    """Attempt the Auto-Tag → pikepdf enhance path.

    Returns enhanced tagged PDF bytes on success, None on any failure.
    """
    try:
        from services.extraction.adobe_client import (
            AdobeExtractClient,
            _AUTO_TAG_AVAILABLE,
        )
    except ImportError:
        logger.debug("Adobe client not importable — skipping Auto-Tag path")
        return None

    if not _AUTO_TAG_AVAILABLE:
        logger.debug("Auto-Tag API not available in SDK — skipping")
        return None

    # Check credentials
    if not settings.adobe_client_id or not settings.adobe_client_secret:
        logger.debug("Adobe credentials not configured — skipping Auto-Tag path")
        return None

    try:
        import tempfile  # noqa: PLC0415

        client = AdobeExtractClient()

        # Write source PDF to temp file for Auto-Tag
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)

        try:
            result = client.auto_tag_pdf_from_path(tmp_path)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        tagged_pdf = result.get("tagged_pdf", b"")
        if not tagged_pdf:
            logger.warning("Auto-Tag returned empty tagged PDF — falling back")
            return None

        tag_count = result.get("tag_count", 0)
        span.set_attribute("output.pdf_method", "auto_tag_enhanced")
        span.set_attribute("output.auto_tag_tags", tag_count)
        logger.info(
            "Auto-Tag succeeded: %d tags, %d bytes tagged PDF",
            tag_count, len(tagged_pdf),
        )

        # Enhance with pipeline data (alt text, /Lang, bookmarks)
        try:
            from services.recompilation.pdf_tag_enhancer import enhance_tagged_pdf
            enhanced = enhance_tagged_pdf(tagged_pdf, ir_doc, collector=collector)
            span.set_attribute("output.enhanced", True)
            span.set_attribute("output.enhanced_size", len(enhanced))
            return enhanced
        except ImportError:
            logger.debug("pdf_tag_enhancer not importable — returning raw Auto-Tag PDF")
            return tagged_pdf
        except Exception as exc:
            logger.warning(
                "PDF enhancement failed (%s) — returning raw Auto-Tag PDF", exc
            )
            return tagged_pdf

    except Exception as exc:
        logger.warning("Auto-Tag path failed (%s) — falling back to reportlab", exc)
        span.set_attribute("output.auto_tag_error", str(exc))
        return None



# ---------------------------------------------------------------------------
# VeraPDF baseline/endline helpers
# ---------------------------------------------------------------------------


def _run_verapdf_baseline(pdf_bytes: bytes) -> VeraPDFResult | None:
    """Run VeraPDF on the original PDF to establish a baseline clause failure count."""
    try:
        from services.common.config import Settings
        _settings = Settings()
        if not _settings.verapdf_enabled:
            return None
    except Exception:
        pass

    client = VeraPDFClient()
    if not client.is_available():
        logger.debug("VeraPDF not available — skipping baseline validation")
        return None

    result = client.validate_pdfua1(pdf_bytes)
    if result:
        logger.info(
            "VeraPDF baseline: compliant=%s errors=%d failed_clauses=%s",
            result.is_compliant, result.error_count, result.failed_clauses,
        )
    return result


def _run_verapdf_endline(
    output_bytes: bytes,
    baseline: VeraPDFResult | None,
) -> VeraPDFResult | None:
    """Run VeraPDF on the output PDF and compare with baseline."""
    client = VeraPDFClient()
    if not client.is_available():
        return None

    result = client.validate_pdfua1(output_bytes)
    if result and baseline:
        delta = baseline.error_count - result.error_count
        logger.info(
            "VeraPDF endline: compliant=%s errors=%d (delta=%+d from baseline=%d)",
            result.is_compliant, result.error_count, -delta, baseline.error_count,
        )
    elif result:
        logger.info(
            "VeraPDF endline: compliant=%s errors=%d (no baseline for comparison)",
            result.is_compliant, result.error_count,
        )
    return result


# ---------------------------------------------------------------------------
# Selective proposal filtering
# ---------------------------------------------------------------------------


def _filter_unapproved_proposals(ir_doc: IRDocument, approved_ids: set[str]) -> None:
    """Revert AI-drafted changes for proposals not in approved_ids.

    For images whose proposal was not approved, reverts alt text to a
    placeholder. This ensures the HITL review decision is respected.
    """
    reverted = 0
    for block in ir_doc.all_blocks():
        if block.block_type == BlockType.IMAGE:
            image_id = block.attributes.get("image_id", "")
            if not image_id:
                continue
            # The proposal ID for images IS the image_id
            if image_id not in approved_ids:
                # Revert to placeholder
                page = block.page_num
                block.attributes["alt"] = (
                    f"[Figure on page {page + 1} — alt text requires review]"
                )
                block.attributes["data-needs-review"] = "true"
                reverted += 1
    if reverted:
        logger.info(
            "Selective filtering: reverted %d unapproved image alt texts", reverted
        )


# ---------------------------------------------------------------------------
# Orchestrator (public API — unchanged signature)
# ---------------------------------------------------------------------------


def convert_pdf_sync(
    pdf_bytes: bytes,
    filename: str,
    output_format: OutputFormat = "html",
    *,
    validation_mode: ValidationMode = ValidationMode.PUBLISH,
    approved_ids: set[str] | None = None,
) -> tuple[bytes, str, str]:
    """Run the full remediation pipeline synchronously.

    Returns (output_bytes, content_type, task_id) where task_id can be
    used to retrieve the remediation audit trail via the fixes-applied API.

    When *validation_mode* is ``ValidationMode.DRAFT`` (used by /api/remediate
    after user approval), validation issues are logged as warnings but never
    raise ``ValidationBlockedError``.  The user has already reviewed the
    analysis and approved remediation — blocking at this stage prevents the
    document from reaching the HITL review step.

    When *approved_ids* is provided (non-None), only remediations whose
    proposal ID is in the set are kept. Unapproved image alt text is reverted
    to a placeholder. When ``None``, all remediations are applied (backward
    compatible).
    """
    stem = Path(filename).stem

    with tracer.start_as_current_span("convert_pdf_sync") as root_span:
        root_span.set_attribute("document.filename", filename)
        root_span.set_attribute("document.size_bytes", len(pdf_bytes))
        root_span.set_attribute("output.format", output_format)

        logger.info("Starting sync conversion: filename=%s format=%s", filename, output_format)

        # Create a per-request task ID and event collector for audit trail
        task_id = str(uuid.uuid4())
        collector = RemediationEventCollector(document_id="", task_id=task_id)

        # Telemetry collector — initialized with placeholder document_id
        # (real ID is set after extraction).  Safe: failures never disrupt pipeline.
        tc = TelemetryCollector(
            document_id="",
            task_id=task_id,
            filename=filename,
            file_size_bytes=len(pdf_bytes),
        )
        tc.set("output_format", output_format)

        _current_stage = "extract"  # Track for error reporting

        try:
            # Stage 1+2: Extract → IR
            tc.start_stage("extract")
            ir_doc = stage_extract(pdf_bytes, filename)
            tc.end_stage()

            root_span.set_attribute("document.id", ir_doc.document_id)
            collector.document_id = ir_doc.document_id
            tc.set("document_id", ir_doc.document_id)
            tc.set("page_count", ir_doc.page_count)

            # Collect extraction metrics from IR
            all_blocks = ir_doc.all_blocks()
            tc.set("blocks_extracted", len(all_blocks))
            tc.set("images_found", sum(
                1 for b in all_blocks if b.block_type == BlockType.IMAGE
            ))
            tc.set("tables_found", sum(
                1 for b in all_blocks if b.block_type == BlockType.TABLE
            ))
            tc.set("headings_found", sum(
                1 for b in all_blocks if b.block_type == BlockType.HEADING
            ))

            # Deduplicate tables per page (Adobe Extract sometimes returns duplicates)
            pre_dedup = len(all_blocks)
            for page in ir_doc.pages:
                page.blocks = dedupe_tables_in_page(page.blocks)

            # Remove running header/footer artifacts that Adobe didn't tag
            ir_doc = drop_running_artifacts(ir_doc)
            post_filter = len(ir_doc.all_blocks())
            tc.set("artifacts_filtered", pre_dedup - post_filter)

            # VeraPDF baseline (before remediation)
            baseline_verapdf = _run_verapdf_baseline(pdf_bytes)

            # Persist baseline to database for API access
            if baseline_verapdf is not None:
                try:
                    from services.common.database import get_db
                    get_db().insert_baseline_validation(
                        task_id=task_id,
                        document_id=ir_doc.document_id,
                        pdf_size_bytes=len(pdf_bytes),
                        is_compliant=baseline_verapdf.is_compliant,
                        total_rules_checked=baseline_verapdf.total_rules_checked,
                        passed_rules=baseline_verapdf.passed_rules,
                        error_count=baseline_verapdf.error_count,
                        failed_clauses=baseline_verapdf.failed_clauses,
                        failed_rules=[
                            r.model_dump() for r in baseline_verapdf.failed_rules
                        ],
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist baseline validation for task %s",
                        task_id, exc_info=True,
                    )

            # Stage 2b: AI alt text (if Vertex AI is configured)
            _current_stage = "ai"
            tc.start_stage("ai")
            ir_doc = stage_ai_alt_text(ir_doc, collector=collector)
            tc.end_stage()

            # Collect AI metrics from the collector's events
            tc.set("ai_model", settings.vertex_ai_model or "")
            ai_events = [
                e for e in collector.events()
                if e.component == RemediationComponent.ALT_TEXT
            ]
            ai_succeeded = sum(1 for e in ai_events if e.after)
            tc.set("ai_alt_text_attempted", len(ai_events))
            tc.set("ai_alt_text_succeeded", ai_succeeded)
            tc.set("ai_alt_text_failed", len(ai_events) - ai_succeeded)

            # --- Selective proposal filtering ---
            if approved_ids is not None:
                _filter_unapproved_proposals(ir_doc, approved_ids)

            # Stage 3: IR → HTML
            _current_stage = "build_html"
            tc.start_stage("build_html")
            title = f"{stem} — WCAG Remediated"
            html_content, builder = stage_build_html(ir_doc, title, collector=collector)
            tc.end_stage()

            # Stage 4a: G3 gate — structural HTML validation (blocks on P0 failures)
            _current_stage = "validate"
            tc.start_stage("validate")
            g3_result = run_gate_g3(html_content, mode=validation_mode)
            tc.set("gate_g3_passed", 1 if g3_result.passed else 0)
            if not g3_result.passed:
                p0_failures = [
                    c for c in g3_result.checks
                    if c.status == "hard_fail" and c.priority == "P0"
                ]
                if p0_failures and validation_mode == ValidationMode.PUBLISH:
                    failure_details = "; ".join(c.details for c in p0_failures)
                    logger.error(
                        "G3 gate BLOCKED output: %d P0 failures — %s",
                        len(p0_failures), failure_details,
                    )
                    raise ValidationBlockedError(
                        f"Output blocked by G3 validation: {failure_details}",
                        violations=[c.model_dump() for c in p0_failures],
                    )
                elif p0_failures:
                    failure_details = "; ".join(c.details for c in p0_failures)
                    logger.warning(
                        "G3 gate: %d P0 failures (non-blocking — user approved): %s",
                        len(p0_failures), failure_details,
                    )
                # Non-P0 failures are logged but don't block
                p1_failures = [c for c in g3_result.checks if c.status != "pass"]
                if p1_failures:
                    logger.warning(
                        "G3 gate: %d non-blocking issues found",
                        len(p1_failures),
                    )

            # Stage 4b: Built-in validate_accessibility (scoring + banner)
            validation = stage_validate(builder, html_content, mode=validation_mode)
            score = validation.get("score", 0)
            tc.set("axe_score", score)
            tc.set("axe_violations_critical", len(validation.get("critical_violations", [])))
            tc.set("axe_violations_serious", len(validation.get("serious_violations", [])))
            tc.set("validation_blocked", 1 if validation.get("blocked") else 0)
            tc.end_stage()

            # Enforce blocking from validate_accessibility too
            if validation.get("blocked"):
                critical = validation.get("critical_violations", [])
                serious = validation.get("serious_violations", [])
                all_labels = critical + serious
                if validation_mode == ValidationMode.PUBLISH:
                    logger.error(
                        "Validation BLOCKED delivery: score=%.2f critical=%s serious=%s",
                        score, critical, serious,
                    )
                    raise ValidationBlockedError(
                        f"Output blocked by validation (score={score:.0%}): "
                        + "; ".join(all_labels),
                        violations=validation.get("violations", []),
                    )
                else:
                    logger.warning(
                        "Validation issues (non-blocking — user approved): "
                        "score=%.2f critical=%s serious=%s",
                        score, critical, serious,
                    )

            # Stage 4c: VeraPDF endline placeholder (populated after stage_output for PDF)
            endline_verapdf: VeraPDFResult | None = None

            # Stage 4d: Inject validation summary into HTML
            html_content = _inject_validation_summary(
                html_content, validation,
                baseline_verapdf=baseline_verapdf,
                endline_verapdf=endline_verapdf,
            )

            # Stage 5: Output
            _current_stage = "output"
            tc.start_stage("output")
            output_bytes, content_type = stage_output(
                html_content, output_format, builder,
                pdf_bytes=pdf_bytes, ir_doc=ir_doc, collector=collector,
            )
            tc.end_stage()
            tc.set("output_size_bytes", len(output_bytes))

            # VeraPDF endline (after remediation, PDF only)
            if output_format == "pdf":
                endline_verapdf = _run_verapdf_endline(output_bytes, baseline_verapdf)

            # Regression gate: if output has MORE errors than input, warn loudly
            if endline_verapdf and baseline_verapdf:
                baseline_errors = baseline_verapdf.error_count
                endline_errors = endline_verapdf.error_count
                if endline_errors > baseline_errors:
                    regression_delta = endline_errors - baseline_errors
                    regression_msg = (
                        f"REGRESSION DETECTED: output PDF has {endline_errors} VeraPDF errors "
                        f"vs {baseline_errors} in input (delta=+{regression_delta}). "
                        f"Remediation made the document LESS compliant."
                    )
                    logger.error(regression_msg)
                    root_span.set_attribute("verapdf.regression", True)
                    root_span.set_attribute("verapdf.regression_delta", regression_delta)
                    # Record regression in remediation events for the audit trail
                    if collector:
                        from services.common.remediation_events import RemediationComponent
                        collector.record(
                            RemediationComponent.MARK_INFO,
                            before=f"baseline_errors={baseline_errors}",
                            after=f"endline_errors={endline_errors}",
                            source="verapdf_regression_gate",
                        )
                    # Optionally block output when regression gate is set to blocking
                    if settings.regression_gate_blocking:
                        raise ValidationBlockedError(regression_msg)
                else:
                    improvement = baseline_errors - endline_errors
                    logger.info(
                        "VeraPDF regression gate: PASS — output has %d fewer errors than input "
                        "(%d → %d)",
                        improvement, baseline_errors, endline_errors,
                    )
                    root_span.set_attribute("verapdf.regression", False)
                    root_span.set_attribute("verapdf.improvement", improvement)

            root_span.set_attribute("output.size_bytes", len(output_bytes))
            root_span.set_attribute("validation.score", score)
            logger.info(
                "Conversion complete: document_id=%s format=%s size=%d bytes score=%.2f",
                ir_doc.document_id, output_format, len(output_bytes), score,
            )

            # Persist remediation events to the in-memory cache for audit trail retrieval
            _cache_remediation_events(task_id, collector.to_dict_list())
            root_span.set_attribute("remediation.event_count", len(collector.events()))
            root_span.set_attribute("remediation.task_id", task_id)

            # Mark telemetry as successful and persist
            tc.mark_success()
            tc.persist(get_db(settings.db_path))

            return output_bytes, content_type, task_id

        except ValidationBlockedError:
            # Validation blocks are expected control flow — record but re-raise
            tc.mark_failed("Validation blocked output", _current_stage)
            tc.persist(get_db(settings.db_path))
            raise
        except Exception as exc:
            tc.mark_failed(str(exc), _current_stage)
            tc.persist(get_db(settings.db_path))
            raise


# ---------------------------------------------------------------------------
# Validation Summary Injection
# ---------------------------------------------------------------------------


def _inject_validation_summary(
    html: str,
    validation: dict[str, Any],
    *,
    baseline_verapdf: VeraPDFResult | None = None,
    endline_verapdf: VeraPDFResult | None = None,
) -> str:
    """Inject a validation summary into the HTML output.

    Adds:
    1. An HTML comment with machine-readable validation metadata (including
       ``Blocked:``, ``Critical:``, and ``Serious:`` lines).
    2. A visible banner after ``<main>`` showing the score and any violations.
       When ``validation["blocked"]`` is True the banner uses a red "BLOCKED"
       badge and lists the critical violations prominently at the top.
    3. An optional VeraPDF PDF/UA-1 section when baseline/endline data are available.

    The banner is informational only — it does NOT stop HTML generation.
    """
    score = validation.get("score", 0)
    violations = validation.get("violations", [])
    total_checks = validation.get("total_checks", 0)
    passed_checks = validation.get("passed_checks", 0)
    blocked = validation.get("blocked", False)
    critical_violations = validation.get("critical_violations", [])
    serious_violations = validation.get("serious_violations", [])

    # --- Determine badge ---
    # Colors chosen for WCAG 4.5:1 contrast against white background.
    if blocked:
        badge = "BLOCKED"
        badge_color = "#b91c1c"  # red-700 — 6.05:1 contrast
    elif score >= 0.95:
        badge = "PASS"
        badge_color = "#15803d"  # green-700 — 4.59:1 contrast
    elif score >= 0.85:
        badge = "REVIEW"
        badge_color = "#a16207"  # yellow-700 — 4.78:1 contrast
    else:
        badge = "FAIL"
        badge_color = "#b91c1c"  # red-700 — 6.05:1 contrast

    # --- Build machine-readable HTML comment ---
    comment_lines = [
        "<!-- WCAG VALIDATION SUMMARY",
        f"  Score: {score:.2f}",
        f"  Badge: {badge}",
        f"  Blocked: {str(blocked).lower()}",
        f"  Critical: {len(critical_violations)}"
        + (f" ({'; '.join(critical_violations)})" if critical_violations else ""),
        f"  Serious: {len(serious_violations)}"
        + (f" ({'; '.join(serious_violations)})" if serious_violations else ""),
        f"  Checks: {passed_checks}/{total_checks} passed",
        f"  Violations: {len(violations)}",
    ]
    for v in violations[:10]:  # Cap at 10 for comment readability
        desc = v.get("description", v.get("criterion", "unknown"))
        sev = v.get("severity", "unknown")
        vclass = v.get("violation_class", sev)
        comment_lines.append(f"  - [{vclass}/{sev}] {desc}")
    if len(violations) > 10:
        comment_lines.append(f"  ... and {len(violations) - 10} more")
    comment_lines.append("-->")
    comment_block = "\n".join(comment_lines)

    # --- Build visible critical-violations section (shown when blocked) ---
    critical_section = ""
    if blocked and (critical_violations or serious_violations):
        blocking_items = [
            f'<li style="margin-bottom:4px;">{item}</li>'
            for item in (critical_violations + serious_violations)
        ]
        critical_section = (
            f'<p style="margin:0.5em 0 0.25em;font-weight:600;">'
            f'Blocking issues that require resolution:</p>'
            f'<ul style="margin:0.25em 0 0;padding-left:1.5em;">'
            f'{"".join(blocking_items)}'
            f'</ul>'
        )

    # --- Build non-critical violations list ---
    non_blocking_violations = [
        v for v in violations
        if v.get("violation_class") not in ("critical",)
        or not blocked
    ]
    violation_items = ""
    if non_blocking_violations:
        items = []
        for v in non_blocking_violations[:5]:
            desc = v.get("description", v.get("criterion", "unknown"))
            sev = v.get("severity", "unknown")
            vclass = v.get("violation_class", sev)
            items.append(f'<li>[{vclass}/{sev}] {desc}</li>')
        if len(non_blocking_violations) > 5:
            items.append(f"<li>... and {len(non_blocking_violations) - 5} more</li>")
        if items:
            label = "Other issues:" if blocked else "Issues:"
            violation_items = (
                f'<p style="margin:0.5em 0 0.25em;font-weight:600;">{label}</p>'
                f'<ul style="margin:0.25em 0 0;padding-left:1.5em;">{"".join(items)}</ul>'
            )

    # --- Compose the banner ---
    if blocked:
        headline = (
            f'<strong style="color:{badge_color};font-size:16px;">&#x26A0; WCAG Validation: BLOCKED</strong>'
            f' — Score: {score:.0%} — Output requires HITL review before use'
        )
    else:
        headline = (
            f'<strong style="color:{badge_color};">WCAG Validation: {badge}</strong>'
            f' — Score: {score:.0%} ({passed_checks}/{total_checks} checks passed)'
        )

    # --- VeraPDF comparison section ---
    verapdf_section = ""
    if baseline_verapdf or endline_verapdf:
        verapdf_html = (
            '<div style="margin-top:8px;padding:8px;border:1px solid #d1d5db;'
            'border-radius:4px;">'
            '<strong>PDF/UA-1 Compliance (VeraPDF)</strong><br>'
        )
        if baseline_verapdf:
            verapdf_html += (
                f'Baseline: {baseline_verapdf.error_count} errors across '
                f'{len(baseline_verapdf.failed_clauses)} clauses<br>'
            )
        if endline_verapdf:
            verapdf_html += (
                f'After remediation: {endline_verapdf.error_count} errors across '
                f'{len(endline_verapdf.failed_clauses)} clauses<br>'
            )
        if baseline_verapdf and endline_verapdf:
            delta = baseline_verapdf.error_count - endline_verapdf.error_count
            if delta > 0:
                verapdf_html += (
                    f'<span style="color:#15803d;">Improved: {delta} fewer errors</span>'
                )
            elif delta < 0:
                verapdf_html += (
                    f'<span style="color:#b91c1c;">Regressed: {-delta} more errors</span>'
                )
            else:
                verapdf_html += 'No change in error count'
        verapdf_html += '</div>'
        verapdf_section = verapdf_html

    banner = (
        f'<aside role="note" aria-label="Accessibility validation summary"'
        f' style="border:2px solid {badge_color};border-radius:8px;'
        f'padding:12px 16px;margin:16px 0;background:{badge_color}11;'
        f'font-family:system-ui,sans-serif;font-size:14px;">'
        f'{headline}'
        f'{critical_section}'
        f'{violation_items}'
        f'{verapdf_section}'
        f'</aside>'
    )

    # Inject: comment before </body>, banner after <main>
    html = html.replace("</body>", f"{comment_block}\n</body>")
    html = html.replace("<main>", f"<main>\n{banner}")

    return html


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _run_extraction(local_pdf: Path) -> dict[str, Any]:
    """Call Adobe Extract API on a local PDF file.

    For documents exceeding Adobe API page limits, automatically chunks the
    PDF using pikepdf, processes each chunk independently, and merges results.
    """
    try:
        from services.extraction.adobe_client import AdobeExtractClient, _SDK_AVAILABLE

        with tracer.start_as_current_span("extraction.adobe_api") as span:
            span.set_attribute("extraction.sdk_available", _SDK_AVAILABLE)
            span.set_attribute("extraction.method", "adobe")
            span.set_attribute("extraction.pdf_path", str(local_pdf))
            logger.info("Adobe SDK available: %s", _SDK_AVAILABLE)

            client = AdobeExtractClient()

            # Check if document needs chunking (exceeds Adobe page limits)
            try:
                from services.ingestion.chunker import needs_chunking, chunk_pdf, merge_extraction_results, get_page_count

                if needs_chunking(local_pdf):
                    total_pages = get_page_count(local_pdf)
                    logger.info(
                        "Document exceeds Adobe page limit (%d pages). Chunking...",
                        total_pages,
                    )
                    span.set_attribute("extraction.chunked", True)
                    span.set_attribute("extraction.total_pages", total_pages)

                    chunks = chunk_pdf(local_pdf, max_pages=250, overlap=2)
                    if chunks:
                        chunk_results = []
                        for chunk in chunks:
                            logger.info(
                                "Processing chunk %d/%d (pages %d-%d)",
                                chunk.chunk_index + 1, len(chunks),
                                chunk.start_page, chunk.end_page - 1,
                            )
                            chunk_json = client.extract_pdf_from_path(chunk.pdf_path)
                            chunk_results.append((chunk, chunk_json))

                        result = merge_extraction_results(chunk_results, total_pages)
                        elements = result.get("elements", [])
                        span.set_attribute("extraction.chunks", len(chunks))
                        span.set_attribute("extraction.elements_count", len(elements))
                        logger.info(
                            "Chunked extraction complete: %d chunks → %d elements",
                            len(chunks), len(elements),
                        )
                        return result
            except ImportError:
                logger.debug("Chunker module not available — skipping chunking check")

            result = client.extract_pdf_from_path(local_pdf)
            elements = result.get("elements", [])
            table_count = sum(1 for e in elements if "/Table" in e.get("Path", ""))
            span.set_attribute("extraction.elements_count", len(elements))
            span.set_attribute("extraction.table_elements", table_count)
            logger.info("Adobe Extract succeeded: %d elements (%d table)", len(elements), table_count)
            return result

    except ImportError as exc:
        # SDK not installed — only acceptable in test environments.
        # Fall back to pypdf for basic extraction.
        logger.warning("Adobe SDK not installed (%s). Falling back to pypdf.", exc)
        with tracer.start_as_current_span("extraction.pypdf_fallback") as span:
            span.set_attribute("extraction.method", "pypdf_fallback")
            span.set_attribute("extraction.fallback_reason", f"ImportError: {exc}")
            result = _fallback_extraction(local_pdf)
            span.set_attribute("extraction.elements_count", len(result.get("elements", [])))
            return result

    except Exception as exc:
        # Adobe API failed (credentials, network, permissions, etc.)
        # Do NOT silently fall back — the output would be garbage.
        logger.error(
            "Adobe Extract FAILED (%s: %s). Refusing to deliver degraded output.",
            type(exc).__name__, exc,
        )
        raise RuntimeError(
            f"Adobe Extract API failed: {type(exc).__name__}: {exc}. "
            f"Cannot produce quality output without structural extraction. "
            f"Check Adobe credentials and API connectivity."
        ) from exc


def _fallback_extraction(local_pdf: Path) -> dict[str, Any]:
    """Basic fallback using pypdf (no structural awareness).

    Heading hierarchy heuristic:
      - ALL CAPS + short (<60 chars) → H1 (document/section title)
      - ALL CAPS + medium (<120 chars) → H2 (sub-section)
      - Title Case + short (<80 chars) → H3 (sub-sub-section)
      - Everything else → paragraph
    """
    elements: list[dict[str, Any]] = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(local_pdf))
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
                if para.isupper() and len(para) < 60:
                    path = "//Document/H1"
                elif para.isupper() and len(para) < 120:
                    path = "//Document/H2"
                elif para.istitle() and len(para) < 80 and "\n" not in para:
                    path = "//Document/H3"
                else:
                    path = "//Document/P"
                elements.append({"Path": path, "Text": para, "Page": page_num})
        logger.info("Fallback: %d elements from %d pages", len(elements), len(reader.pages))
    except ImportError:
        logger.warning("pypdf not installed.")
    except Exception as exc:
        logger.warning("Fallback extraction failed: %s", exc)
    return {"elements": elements}


# ---------------------------------------------------------------------------
# Legacy elements → IR conversion
# ---------------------------------------------------------------------------

_BLOCK_TYPE_MAP = {
    "heading": BlockType.HEADING,
    "paragraph": BlockType.PARAGRAPH,
    "table": BlockType.TABLE,
    "image": BlockType.IMAGE,
    "list": BlockType.LIST,
    "form_field": BlockType.FORM_FIELD,
}


def _elements_to_ir(
    elements: list[dict[str, Any]],
    extract_json: dict[str, Any],
    document_id: str,
    filename: str,
) -> IRDocument:
    """Convert legacy element list + raw Adobe JSON into an IRDocument.

    Groups elements by page (from the raw Adobe JSON when available).
    Falls back to a single page if no page info is present.
    """
    # Build a page map from raw Adobe elements for page-level metadata
    raw_elements = extract_json.get("elements", [])
    page_nums: set[int] = set()
    for raw in raw_elements:
        p = raw.get("Page", 0)
        page_nums.add(p)

    if not page_nums:
        page_nums = {0}

    # Convert each legacy element to an IRBlock.
    # Page info is propagated from the Adobe Extract JSON through
    # _reconstruct_document() into each element's attributes["page"].
    blocks_by_page: dict[int, list[IRBlock]] = {}
    for elem in elements:
        raw_type = elem.get("type", "paragraph")
        block_type = _BLOCK_TYPE_MAP.get(raw_type, BlockType.PARAGRAPH)
        elem_page = elem.get("attributes", {}).get("page", 0)
        block = IRBlock(
            block_type=block_type,
            content=elem.get("content", ""),
            source=BlockSource.ADOBE,
            page_num=elem_page,
            confidence=1.0,
            remediation_status=RemediationStatus.RAW,
            attributes=elem.get("attributes", {}),
        )
        blocks_by_page.setdefault(elem_page, []).append(block)

    # Build IRPages grouped by page number
    if blocks_by_page:
        ir_pages = [
            IRPage(page_num=p, blocks=blocks_by_page[p])
            for p in sorted(blocks_by_page)
        ]
    else:
        ir_pages = [IRPage(page_num=0, blocks=[])]

    return IRDocument(
        document_id=document_id,
        filename=filename,
        page_count=max(page_nums) + 1 if page_nums else 1,
        pages=ir_pages,
        language="en",
    )


# ---------------------------------------------------------------------------
# Document reconstruction
# ---------------------------------------------------------------------------

_RE_TABLE_BASE = re.compile(r"(.*?/Table(?:\[\d+\])?)(?:/|$)")
_RE_TABLE_CELL = re.compile(r"/TR(?:\[(\d+)\])?/(TH|TD)(?:\[(\d+)\])?")
_RE_HEADING = re.compile(r"/H(\d)(?:\[\d+\])?(?:/|$)")
_RE_FIGURE = re.compile(r"/Figure(?:\[\d+\])?(?:/|$)")
_RE_LIST = re.compile(r"/L(?:\[\d+\])?(?:/|$)")
_RE_LIST_LABEL = re.compile(r"/Lbl(?:\[\d+\])?(?:/|$)")  # Skip bullet/number labels
_RE_TOC = re.compile(r"/TOC(?:\[\d+\])?(?:/|$)")  # Table of Contents — skip (content duplicated in body)
_RE_FOOTNOTE = re.compile(r"/Footnote(?:\[\d+\])?(?:/|$)")

# Text cleanup patterns
_RE_XREF_ARTIFACT = re.compile(r"\s*\(<>\)\s*")
# Bullet patterns for list accumulation
_RE_BULLET = re.compile(r"^[\u2022\u2023\u25E6\u25AA\u25CF\u25CB\u2013\u2014\u2010•·\-\*]\s+")
_RE_NUMBERED = re.compile(r"^(?:\d{1,3}[.)]\s+|\([a-zA-Z0-9]+\)\s+|[a-zA-Z][.)]\s+)")

# Common English word suffixes that indicate a broken word when separated
# e.g., "JANU ARY" → "JANUARY", "PROGRAMMATI C" → "PROGRAMMATIC"
_WORD_SUFFIXES = frozenset({
    "AL", "AN", "AR", "ARY", "ATE", "ATED", "ATION", "ATIONS",
    "C", "CE", "CES", "CTION", "CTURE",
    "D", "ED", "ENCE", "ENT", "ER", "ERN", "ERS", "ES",
    "FUL",
    "GE",
    "IC", "ICS", "IDE", "IES", "ILY", "ING", "ION", "IONS", "ISH", "ISM",
    "IST", "ITY", "IVE",
    "LY",
    "MENT", "MENTS",
    "NESS",
    "ON", "ORS", "OUS", "OWN",
    "RY",
    "S", "SE", "SES", "SION", "SIONS",
    "TION", "TIONS", "TH", "TURE", "TURES", "TY",
    "URE", "URES", "US",
    "Y",
})


# ---------------------------------------------------------------------------
# Numbering-depth heading heuristic (government document sections)
# ---------------------------------------------------------------------------

# Patterns for numbered section headings common in government documents
_RE_NUM_DOTTED = re.compile(r"^(\d+\.(?:\d+\.?)*)\s")     # "1. ", "1.1 ", "1.1.1 "
_RE_ALPHA_DOTTED = re.compile(r"^([A-Z]\.(?:\d+\.?)*)\s")  # "A. ", "A.1 ", "A.1.1 "
_RE_PAREN_NUM = re.compile(r"^\(\d+\)\s")                   # "(1) " → depth 1
_RE_PAREN_ALPHA = re.compile(r"^\([a-z]\)\s")               # "(a) " → depth 2 (sub-item)


def _numbering_depth(text: str) -> int:
    """Detect government-style numbered section depth from heading text.

    Returns the depth (number of hierarchical components):
        "1."   → 1  (→ H2)
        "1.1"  → 2  (→ H3)
        "1.1.1"→ 3  (→ H4)
        "A."   → 1  (→ H2)
        "A.1"  → 2  (→ H3)
        "(1)"  → 1  (→ H2)
        "(a)"  → 2  (→ H3)
        No match → 0 (use font-size based level)
    """
    m = _RE_NUM_DOTTED.match(text)
    if m:
        # Split on dots, filter empty: "1." → ["1"] → 1, "1.1" → ["1","1"] → 2
        prefix = m.group(1).rstrip(".")
        return len(prefix.split("."))

    m = _RE_ALPHA_DOTTED.match(text)
    if m:
        # "A." → ["A"] → 1, "A.1" → ["A","1"] → 2, "A.1.1" → 3
        prefix = m.group(1).rstrip(".")
        return len(prefix.split("."))

    if _RE_PAREN_NUM.match(text):
        return 1

    if _RE_PAREN_ALPHA.match(text):
        return 2

    return 0


def _clean_text(text: str) -> str:
    """Clean extracted text: remove cross-reference artifacts and fix broken words.

    Conservative approach — only merges words when:
    1. Single spaced-out letters (3+): "I N F R A" → "INFRA"
    2. A CAPS fragment followed by a short CAPS suffix: "JANU ARY" → "JANUARY"
    Never merges two legitimate separate words.
    """
    # Remove (<>) cross-reference artifacts
    text = _RE_XREF_ARTIFACT.sub("", text)
    # Fix spaced-out single letters: "I N F R A S T R U C T U R E" → "INFRASTRUCTURE"
    text = re.sub(
        r"\b([A-Z](?:\s[A-Z]){2,})\b",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    # Fix mid-word breaks: merge ONLY when the second fragment is a known suffix
    # e.g., "JANU ARY" → "JANUARY", "PROGRAMMATI C" → "PROGRAMMATIC"
    text = re.sub(r"\b([A-Z]{2,})\s+([A-Z]{1,6})\b", _merge_if_suffix, text)
    # Fix two-part splits like "FIGUR E S" → "FIGURES" (fragment + single letter + S)
    text = re.sub(r"\b([A-Z]{3,})\s([A-Z])\s([A-Z])\b", _merge_three_part, text)
    # Remove print artifacts
    text = re.sub(r'\b\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[A-Z]:\\[^\s]+', '', text)  # Windows file paths
    text = re.sub(r'\bPage\s+\d+\s+of\s+\d+\b', '', text, flags=re.IGNORECASE)
    # Remove encoding noise
    text = text.replace('\ufffd', '')  # Unicode replacement character
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)  # Control characters (except \t\n\r)
    return text.strip()


def _merge_three_part(match: re.Match) -> str:
    """Merge three-part splits like 'FIGUR E S' where middle and end are single chars."""
    a, b, c = match.group(1), match.group(2), match.group(3)
    # Only merge if the middle+end form a common word ending
    suffix = b + c
    if suffix in {"ES", "ED", "ER", "LY", "AL", "IC", "ON", "RY", "TH", "LE", "RE", "TS", "NS"}:
        return a + suffix
    return match.group(0)


def _merge_if_suffix(match: re.Match) -> str:
    """Merge two ALL-CAPS fragments only if the second is a known word suffix."""
    a, b = match.group(1), match.group(2)
    if b in _WORD_SUFFIXES:
        return a + b
    return match.group(0)  # Keep separate — these are likely two real words


def _merge_fragment_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pre-pass: merge single-character text fragments with adjacent elements.

    Adobe Extract sometimes splits a word across two elements, e.g.:
      Element 1: Path="//Document/P[42]", Text="I"
      Element 2: Path="//Document/P[43]", Text="NFRASTRUC TU R E ..."

    This pre-pass merges Element 1's text into Element 2 when:
    - Element 1 has 1-2 characters of text (just a fragment)
    - Element 2 starts with uppercase letters (continuation)
    - Both are paragraph-like (not headings, figures, or table cells)
    """
    if len(elements) < 2:
        return elements

    result: list[dict[str, Any]] = []
    skip_next = False

    for i, elem in enumerate(elements):
        if skip_next:
            skip_next = False
            continue

        text = (elem.get("Text") or "").strip()
        path = elem.get("Path", "")

        # Check if this is a tiny text fragment that should merge forward
        if (
            len(text) <= 2
            and text.isupper()
            and i + 1 < len(elements)
            and not _RE_HEADING.search(path)
            and not _RE_FIGURE.search(path)
            and "/Table" not in path
        ):
            next_elem = elements[i + 1]
            next_text = (next_elem.get("Text") or "").strip()
            next_path = next_elem.get("Path", "")

            # Merge if the next element is also a paragraph with uppercase start
            if (
                next_text
                and next_text[0].isupper()
                and not _RE_HEADING.search(next_path)
                and not _RE_FIGURE.search(next_path)
                and "/Table" not in next_path
            ):
                # Prepend this fragment to the next element
                merged = dict(next_elem)
                merged["Text"] = text + next_text
                result.append(merged)
                skip_next = True
                continue

        result.append(elem)

    return result


def _smooth_heading_sequence(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-process heading elements to ensure no heading level is skipped (WCAG 2.4.6).

    Scans headings in document order and ensures the hierarchy never jumps by
    more than 1 level from the previous heading. For example, H2 followed by H4
    becomes H2 followed by H3.

    Rules:
        - The first heading is always accepted as-is
        - If a heading's level jumps by more than 1 from the previous heading,
          it is demoted to (previous_level + 1)
        - Headings are never promoted (H3 is never changed to H2)
        - Non-heading elements are passed through unchanged

    Example: [H1, H2, H4, H5, H2, H6] → [H1, H2, H3, H4, H2, H3]
    """
    last_heading_level: int | None = None

    for elem in elements:
        if elem.get("type") != "heading":
            continue

        attrs = elem.get("attributes", {})
        level = attrs.get("level")
        if level is None:
            continue

        if last_heading_level is None:
            # First heading — accept as-is
            last_heading_level = level
            continue

        max_allowed = last_heading_level + 1
        if level > max_allowed:
            # Demote: e.g., H4 after H2 → H3
            attrs["level"] = max_allowed
            level = max_allowed

        last_heading_level = level

    return elements


def _enforce_heading_hierarchy(elements: list[dict[str, Any]]) -> None:
    """Post-process heading levels to enforce a valid hierarchy (modifies in place).

    Rules applied in order:

    1. **Single H1 / title merging**: Only the first heading(s) with the original
       level 1 become H1.  If consecutive H1 headings appear at the very start of
       the document (before any non-heading element intervenes) they are merged
       into a single H1 joined with " -- ".  All subsequent headings that were
       also assigned level 1 by the size-based inference are demoted to level 2.

    2. **Monotonic enforcement**: Walk headings in order and track the current
       section level.  No child heading may have a *lower* level number (higher
       importance) than would be valid given the current nesting depth.  If
       violated the child is demoted to ``parent_level + 1``.

    3. **No level skipping**: After monotonic enforcement, compress the level
       sequence so there are no gaps (e.g. H1 -> H3 becomes H1 -> H2).
    """

    # ------------------------------------------------------------------ helpers
    def _is_heading(elem: dict[str, Any]) -> bool:
        return elem.get("type") == "heading"

    def _level(elem: dict[str, Any]) -> int:
        return (elem.get("attributes") or {}).get("level", 1)

    def _set_level(elem: dict[str, Any], lvl: int) -> None:
        elem.setdefault("attributes", {})["level"] = lvl

    # Collect heading indices for fast iteration
    heading_indices: list[int] = [i for i, e in enumerate(elements) if _is_heading(e)]
    if not heading_indices:
        return

    # -------------------------------------------------------------- Rule 1
    # Find the run of consecutive headings at the START of the document that all
    # have level 1 (before any non-heading element intervenes).
    title_run: list[int] = []
    for idx in heading_indices:
        # Check that no non-heading element appears between the previous heading
        # (or start of doc) and this heading.
        start_scan = (title_run[-1] + 1) if title_run else 0
        has_non_heading_between = any(
            not _is_heading(elements[j]) for j in range(start_scan, idx)
        )
        if has_non_heading_between:
            break
        if _level(elements[idx]) == 1:
            title_run.append(idx)
        else:
            break

    if len(title_run) > 1:
        # Merge consecutive H1 title headings into a single H1
        merged_text = " -- ".join(
            (elements[idx].get("content") or "") for idx in title_run
        )
        # Keep the first element as the merged H1, mark the rest for removal
        elements[title_run[0]]["content"] = merged_text
        _set_level(elements[title_run[0]], 1)
        # Remove merged duplicates (reverse order to preserve indices)
        for idx in sorted(title_run[1:], reverse=True):
            elements.pop(idx)

    # Rebuild heading_indices after potential removal
    heading_indices = [i for i, e in enumerate(elements) if _is_heading(e)]
    if not heading_indices:
        return

    # Demote all remaining level-1 headings (after the first) to level 2
    first_h1_seen = False
    for idx in heading_indices:
        if _level(elements[idx]) == 1:
            if not first_h1_seen:
                first_h1_seen = True
            else:
                _set_level(elements[idx], 2)

    # -------------------------------------------------------------- Rule 2
    # Walk headings: a heading may go UP (less deep) freely, but going DOWN
    # (deeper) must not skip more than 1 level from the current section depth.
    last_level: int | None = None
    for idx in heading_indices:
        level = _level(elements[idx])
        if last_level is None:
            last_level = level
            continue
        max_allowed = last_level + 1
        if level > max_allowed:
            _set_level(elements[idx], max_allowed)
            level = max_allowed
        last_level = level

    # -------------------------------------------------------------- Rule 3
    # Collect all unique levels used, map them to a contiguous 1..N sequence.
    used_levels: list[int] = sorted({_level(elements[i]) for i in heading_indices})
    # Build mapping: original level -> compressed level
    level_map: dict[int, int] = {}
    for new_lvl, old_lvl in enumerate(used_levels, start=1):
        level_map[old_lvl] = min(new_lvl, 6)

    for idx in heading_indices:
        old = _level(elements[idx])
        _set_level(elements[idx], level_map.get(old, old))


_RE_TABLE_CAPTION_TEXT = re.compile(
    r"^(?:TABLE|FIGURE|CHART|EXHIBIT)\s+\d+[A-Za-z]?\s*[:.\-–—]?\s*(.*)", re.IGNORECASE
)


def _is_likely_table_caption(text: str) -> bool:
    """Return True if *text* is likely a table caption label.

    Matches:
      - "Table 1: ..." / "TABLE 1 — ..." / "Table 1" (no separator)
      - "Figure 2: ..." (chart data tables often labeled as figures)
      - "Chart 3 - ..." / "Exhibit A: ..."
      - Short preceding text (<120 chars) that starts with "Table" (case-insensitive)
    """
    if not text:
        return False
    if _RE_TABLE_CAPTION_TEXT.match(text):
        return True
    # Short text starting with "Table" (covers "Table showing ...", etc.)
    if len(text) < 120 and text.lower().startswith("table"):
        return True
    return False


def _attach_table_captions(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-pass: find table caption labels in paragraphs/headings before tables.

    When found, remove the paragraph and set it as the table's ``content`` field,
    which ``_render_table()`` emits as ``<caption>`` (WCAG 1.3.1).
    """
    if len(elements) < 2:
        return elements

    # Identify indices where a caption-like element precedes a table element
    caption_indices: set[int] = set()
    for i in range(len(elements) - 1):
        elem = elements[i]
        if elem["type"] not in ("paragraph", "heading"):
            continue
        text = elem.get("content", "").strip()
        if not _is_likely_table_caption(text):
            continue
        # Look ahead for the next table (may have intervening headings from pre-header rows)
        for j in range(i + 1, min(i + 4, len(elements))):
            if elements[j]["type"] == "table":
                # Only set caption if the table doesn't already have one
                if not elements[j].get("content"):
                    elements[j]["content"] = text
                caption_indices.add(i)
                break

    if not caption_indices:
        return elements

    return [e for idx, e in enumerate(elements) if idx not in caption_indices]


def _infer_heading_levels(elements: list[dict[str, Any]]) -> dict[float, int]:
    """Infer heading levels from TextSize metadata in Adobe Extract elements.

    Adobe Extract marks all headings as H1 in the Path field regardless of actual
    visual hierarchy. This function uses the TextSize field to infer the true
    heading level: largest font size → H1, next → H2, etc.

    When two TextSize values are within 0.5pt of each other, the one with the
    higher font weight wins the higher heading rank (tiebreaker).

    Args:
        elements: Raw Adobe Extract element list (before fragment merging).
                  Must include heading elements with TextSize metadata.

    Returns:
        A mapping of rounded TextSize -> heading level (1-6).
        Returns an empty dict if no TextSize data is available (e.g. pypdf fallback).

    Rules:
        - Only considers elements whose Path contains a heading tag (/H1 through /H6)
        - Rounds TextSize to 1 decimal place to handle floating-point imprecision
        - Collects max font weight per size for tiebreaking
        - Sorts by (-size, -weight): largest font size first, then heaviest weight
        - When two sizes are within 0.5pt, font weight breaks the tie
        - If more than 6 distinct sizes, the smallest sizes all map to H6
    """
    # Collect (rounded_size, max_weight_at_that_size)
    size_weight: dict[float, float] = {}
    for elem in elements:
        path = elem.get("Path", "")
        if not _RE_HEADING.search(path):
            continue
        raw_size = elem.get("TextSize")
        if raw_size is None:
            continue
        try:
            rounded = round(float(raw_size), 1)
        except (TypeError, ValueError):
            continue

        # Extract font weight (default 400 = normal)
        weight = 400.0
        font = elem.get("Font")
        if isinstance(font, dict):
            raw_weight = font.get("weight")
            if raw_weight is not None:
                try:
                    weight = float(raw_weight)
                except (TypeError, ValueError):
                    pass

        # Track the maximum weight seen for this size
        if rounded not in size_weight or weight > size_weight[rounded]:
            size_weight[rounded] = weight

    if not size_weight:
        return {}

    # Sort descending: largest font size gets the highest heading rank (H1).
    # When two sizes are within 0.5pt, use font weight as tiebreaker.
    entries = list(size_weight.items())  # [(size, max_weight), ...]

    def _sort_key(item: tuple[float, float]) -> tuple[float, float]:
        size, weight = item
        return (-size, -weight)

    entries.sort(key=_sort_key)

    # Re-rank: if adjacent entries are within 0.5pt, weight already broke the tie
    # via the sort key, so we just assign ranks in order.
    mapping: dict[float, int] = {}
    for rank, (size, _weight) in enumerate(entries, start=1):
        level = min(rank, 6)  # Cap at H6
        mapping[size] = level

    return mapping


def _extract_images_from_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract all raster images from a PDF using PyMuPDF (fitz).

    Returns a list of dicts with keys:
        page (int), bbox (tuple[float,float,float,float]),
        base64 (str), mime (str)

    Skips images smaller than 20x20 px (decorative dots/lines).
    Caps at 300 images per document to prevent bloat.
    Resizes images wider than 800px and re-encodes so individual images stay
    under ~500 KB after base64 encoding (~375 KB raw threshold).

    Returns an empty list if PyMuPDF is unavailable or an error occurs.
    """
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except ImportError:
        logger.debug("PyMuPDF (fitz) not installed — skipping image extraction.")
        return []

    results: list[dict[str, Any]] = []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning("_extract_images_from_pdf: failed to open %s: %s", pdf_path, exc)
        return []

    MAX_RAW_BYTES = 375 * 1024  # ~500 KB after base64 encoding
    MAX_WIDTH = 800

    try:
        for page_num in range(len(doc)):
            if len(results) >= 300:
                break
            page = doc[page_num]
            image_list = page.get_images(full=True)

            for img_info in image_list:
                if len(results) >= 300:
                    break

                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue

                width = base_image.get("width", 0)
                height = base_image.get("height", 0)

                # Skip tiny decorative images (dots, lines, dividers)
                if width < 20 or height < 20:
                    continue

                img_bytes = base_image.get("image", b"")
                mime = f"image/{base_image.get('ext', 'png')}"
                if not img_bytes:
                    continue

                needs_resize = width > MAX_WIDTH or len(img_bytes) > MAX_RAW_BYTES
                if needs_resize:
                    try:
                        import io as _io  # noqa: PLC0415
                        try:
                            from PIL import Image as _PILImage  # noqa: PLC0415
                            img_obj = _PILImage.open(_io.BytesIO(img_bytes))
                            if width > MAX_WIDTH:
                                new_height = max(1, int(height * MAX_WIDTH / width))
                                img_obj = img_obj.resize(
                                    (MAX_WIDTH, new_height),
                                    _PILImage.LANCZOS,
                                )
                            buf = _io.BytesIO()
                            save_fmt = "PNG" if mime.endswith("png") else "JPEG"
                            img_obj.save(buf, format=save_fmt, optimize=True)
                            img_bytes = buf.getvalue()
                            mime = f"image/{'png' if save_fmt == 'PNG' else 'jpeg'}"
                        except ImportError:
                            pass  # PIL not available — use original bytes
                    except Exception as resize_exc:
                        logger.debug("Image resize failed, using original: %s", resize_exc)

                # Skip if still too large after attempted resize
                if len(img_bytes) > MAX_RAW_BYTES:
                    logger.debug(
                        "Skipping oversized image xref=%d (%d bytes raw)", xref, len(img_bytes)
                    )
                    continue

                encoded = base64.b64encode(img_bytes).decode("ascii")

                # Determine bounding box on page for this image xref
                bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
                try:
                    rects = page.get_image_rects(xref)
                    if rects:
                        r = rects[0]
                        bbox = (r.x0, r.y0, r.x1, r.y1)
                except Exception:
                    pass

                results.append({
                    "page": page_num,
                    "bbox": bbox,
                    "base64": encoded,
                    "mime": mime,
                })
    finally:
        doc.close()

    logger.debug(
        "_extract_images_from_pdf: extracted %d images from %s", len(results), pdf_path
    )
    return results


def _clip_figure_from_pdf(
    pdf_path: Path | None,
    page_num: int,
    bounds: list[float] | None,
) -> str | None:
    """Clip a figure region from the original PDF page using PyMuPDF.

    When Adobe Extract returns a Figure element with bounding box coordinates
    but no image bytes (and PyMuPDF bbox matching also fails), this function
    renders the figure's bounding box region from the original PDF page at
    150 DPI and returns it as a data-URI PNG.

    Returns a ``data:image/png;base64,...`` string on success, None on failure.
    """
    if pdf_path is None or not bounds or len(bounds) != 4:
        return None

    try:
        import fitz  # noqa: PLC0415
    except ImportError:
        return None

    try:
        doc = fitz.open(str(pdf_path))
        if page_num < 0 or page_num >= len(doc):
            doc.close()
            return None

        page = doc[page_num]
        x0, y0, x1, y1 = bounds

        # Validate bounds are reasonable
        if x1 <= x0 or y1 <= y0:
            doc.close()
            return None

        clip_rect = fitz.Rect(x0, y0, x1, y1)

        # Render at 150 DPI (2x default 72 DPI) for decent quality
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, clip=clip_rect)
        png_bytes = pix.tobytes("png")
        doc.close()

        if len(png_bytes) < 100:  # Too small to be a real image
            return None

        b64 = base64.b64encode(png_bytes).decode()
        logger.debug(
            "_clip_figure_from_pdf: clipped page %d bbox %s → %d bytes PNG",
            page_num, bounds, len(png_bytes),
        )
        return f"data:image/png;base64,{b64}"

    except Exception as exc:
        logger.debug("_clip_figure_from_pdf failed: %s", exc)
        return None


def _match_figure_to_image(
    figure_elem: dict[str, Any],
    extracted_images: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match an Adobe figure element to the best extracted PyMuPDF image.

    Matching strategy:
    1. Filter to images on the same page as the figure element.
    2. Among same-page images, pick the one whose bounding box best overlaps
       with the figure's Bounds ([x0, y0, x1, y1] in PDF points).
    3. If bounding box data is missing, return the first same-page image.
    4. If no same-page images exist, return None.

    Adobe Bounds format: [x0, y0, x1, y1] in PDF user-space units (points).
    fitz bbox: (x0, y0, x1, y1) in the same coordinate space.
    """
    if not extracted_images:
        return None

    page_num = figure_elem.get("Page", 0)
    bounds = figure_elem.get("Bounds")  # [x0, y0, x1, y1] or None

    same_page = [img for img in extracted_images if img["page"] == page_num]
    if not same_page:
        return None

    if len(same_page) == 1:
        return same_page[0]

    # Multiple images on the same page — pick the one with the best bbox overlap
    if not bounds or len(bounds) != 4:
        return same_page[0]

    fx0, fy0, fx1, fy1 = bounds
    best_img = None
    best_overlap = -1.0

    for img in same_page:
        ix0, iy0, ix1, iy1 = img["bbox"]
        inter_x0 = max(fx0, ix0)
        inter_y0 = max(fy0, iy0)
        inter_x1 = min(fx1, ix1)
        inter_y1 = min(fy1, iy1)
        if inter_x1 > inter_x0 and inter_y1 > inter_y0:
            overlap = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
        else:
            overlap = 0.0

        if overlap > best_overlap:
            best_overlap = overlap
            best_img = img

    return best_img if best_img is not None else same_page[0]


# ---------------------------------------------------------------------------
# Header / Footer / Artifact Filtering
# ---------------------------------------------------------------------------

# Exact known footer lines (normalized uppercase, whitespace-collapsed).
# Using exact match avoids deleting legitimate body text like "Prepared by DKS Associates".
_ROGUE_FOOTER_EXACT: set[str] = {
    "DKS SACRAMENTO COUNTY LOCAL ROAD SAFETY PLAN",
    "SACRAMENTO COUNTY DEPARTMENT OF TRANSPORTATION",
    "SACDOT",
}


def _norm_text(s: str) -> str:
    """Normalize text for artifact comparison: uppercase, collapse whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def _is_artifact(element: dict[str, Any]) -> bool:
    """Return True if the Adobe Extract element is a header/footer/pagination artifact.

    Adobe Extract marks running headers, footers, and page numbers with specific
    Path components (``/Artifact``, ``/Header``, ``/Footer``) and metadata fields.
    These elements corrupt the reading order if included in the body flow
    (WCAG 1.3.1, 1.3.2).

    Uses exact-match footer set (not substring) to avoid deleting legitimate
    body content like "Prepared by DKS Associates".
    """
    path = element.get("Path") or ""
    text_type = element.get("TextType") or ""
    role = element.get("Role") or ""
    text = (element.get("Text") or "").strip()

    # Primary: Adobe path-based artifact tags
    if any(tag in path for tag in ("/Artifact", "/Header", "/Footer", "/Pagination")):
        return True

    # Secondary: metadata fields
    lower_text_type = text_type.lower()
    lower_role = role.lower()
    if lower_text_type in {"pagination", "artifact", "header", "footer"}:
        return True
    if lower_role in {"artifact", "pagination", "header", "footer"}:
        return True

    # Exact known footer lines (safe — won't match partial body text)
    norm = _norm_text(text)
    if norm in _ROGUE_FOOTER_EXACT:
        return True

    # Isolated page numbers (1-3 digit standalone numbers)
    if text and text.isdigit() and len(text) <= 3:
        return True

    return False


def drop_running_artifacts(ir_doc: IRDocument) -> IRDocument:
    """Remove repeated short text blocks that appear at consistent vertical positions.

    This catches running headers/footers that Adobe did NOT tag as artifacts.
    A text block is considered a "running artifact" if the same normalized text
    appears at the same vertical band (5% of page height) on 8+ pages.

    Only TEXT and HEADING blocks shorter than 40 characters are considered.
    This preserves legitimate repeated content in body paragraphs.
    """
    from collections import defaultdict

    counts: dict[tuple[str, float], int] = defaultdict(int)
    refs: list[tuple[int, int, tuple[str, float]]] = []

    for p_idx, page in enumerate(ir_doc.pages):
        page_h = page.height or 792.0
        for b_idx, block in enumerate(page.blocks):
            if block.block_type not in (BlockType.PARAGRAPH, BlockType.HEADING):
                continue
            txt = _norm_text(block.content)
            if not txt or len(txt) > 40:
                continue
            # Use y1 (top of bbox) for vertical position bucketing
            y1 = block.bbox.y1
            if y1 == 0.0 and block.bbox.y2 == 0.0:
                continue  # No bbox data available
            y_bucket = round((y1 / page_h) / 0.05) * 0.05  # 5% vertical bands
            key = (txt, y_bucket)
            counts[key] += 1
            refs.append((p_idx, b_idx, key))

    # Blocks that appear on 8+ pages at the same position are running artifacts
    repeated = {k for k, c in counts.items() if c >= 8}
    if not repeated:
        return ir_doc

    removed = 0
    mark: set[tuple[int, int]] = set()
    for p_idx, b_idx, key in refs:
        if key in repeated:
            mark.add((p_idx, b_idx))
            removed += 1

    for p_idx, page in enumerate(ir_doc.pages):
        page.blocks = [
            b for b_idx, b in enumerate(page.blocks)
            if (p_idx, b_idx) not in mark
        ]

    if removed:
        logger.info(
            "drop_running_artifacts: removed %d running artifact blocks (%d patterns)",
            removed, len(repeated),
        )

    return ir_doc


# ---------------------------------------------------------------------------
# Visual fidelity: style extraction from Adobe JSON
# ---------------------------------------------------------------------------

_ALIGN_MAP = {"Start": "left", "End": "right", "Center": "center", "Justify": "justify"}


def _extract_element_style(elem: dict[str, Any]) -> dict[str, Any]:
    """Extract visual style info from an Adobe Extract JSON element.

    Returns a dict with CSS-ready properties (empty dict when nothing useful):
    - font_family: str (e.g. "Arial")
    - font_size: float (in points)
    - font_bold: bool
    - font_italic: bool
    - text_align: str ("left", "center", "right", "justify")
    """
    from services.common.config import settings
    if not settings.preserve_source_styles:
        return {}

    style: dict[str, Any] = {}

    font = elem.get("Font")
    if isinstance(font, dict):
        family = (
            font.get("alt_family_name")
            or font.get("family_name")
            or font.get("name")
        )
        if family:
            style["font_family"] = family
        if font.get("weight", 400) >= 600:
            style["font_bold"] = True
        if font.get("italic"):
            style["font_italic"] = True

    raw_size = elem.get("TextSize")
    if raw_size is not None:
        try:
            style["font_size"] = round(float(raw_size), 1)
        except (TypeError, ValueError):
            pass

    attrs = elem.get("attributes") or {}
    align = attrs.get("InlineAlign") or attrs.get("TextAlign")
    if align:
        css_align = _ALIGN_MAP.get(align, align.lower())
        if css_align in ("left", "right", "center", "justify"):
            style["text_align"] = css_align

    return style


def _rgb_float_to_hex(rgb: list[float]) -> str | None:
    """Convert [0.435, 0.184, 0.623] to '#6f2f9f'. Returns None on bad input."""
    if not isinstance(rgb, list) or len(rgb) != 3:
        return None
    try:
        r, g, b = (max(0, min(255, int(x * 255))) for x in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Form field extraction via pikepdf
# ---------------------------------------------------------------------------


def _extract_form_fields(pdf_path: Path) -> list[dict[str, Any]]:
    """Enumerate AcroForm fields from a PDF using pikepdf.

    Returns a list of element dicts compatible with _reconstruct_document output,
    one per form field found.
    """
    try:
        import pikepdf
    except ImportError:
        logger.warning("pikepdf not available — skipping form field extraction")
        return []

    fields: list[dict[str, Any]] = []
    try:
        with pikepdf.open(pdf_path) as pdf:
            acroform = pdf.Root.get("/AcroForm")
            if not acroform:
                return []

            raw_fields = acroform.get("/Fields", [])
            for field_ref in raw_fields:
                try:
                    field_obj = pdf.get_object(field_ref) if hasattr(field_ref, 'objgen') else field_ref
                    field_type_raw = str(field_obj.get("/FT", ""))
                    field_name = str(field_obj.get("/T", ""))
                    tooltip = str(field_obj.get("/TU", ""))
                    rect = field_obj.get("/Rect")

                    # Map PDF field type to human-readable name
                    type_map = {"/Tx": "text", "/Btn": "checkbox", "/Ch": "dropdown", "/Sig": "signature"}
                    field_type = type_map.get(field_type_raw, "text")

                    # Check for radio buttons (Btn with /Ff flag bit 16 set)
                    if field_type == "checkbox":
                        ff = int(field_obj.get("/Ff", 0))
                        if ff & (1 << 15):
                            field_type = "radio"

                    # Determine page number from widget annotation
                    page_num = 0
                    if rect:
                        # Try to determine page from /P reference
                        page_ref = field_obj.get("/P")
                        if page_ref:
                            for idx, pg in enumerate(pdf.pages):
                                if pg.objgen == page_ref.objgen:
                                    page_num = idx
                                    break

                    fields.append({
                        "type": "form_field",
                        "content": tooltip or field_name,
                        "attributes": {
                            "field_type": field_type,
                            "field_name": field_name,
                            "tooltip": tooltip,
                            "required": bool(int(field_obj.get("/Ff", 0)) & 2),
                            "page": page_num,
                        },
                    })
                except Exception as exc:
                    logger.debug("Skipping malformed form field: %s", exc)
                    continue

    except Exception as exc:
        logger.warning("Form field extraction failed: %s", exc)
        return []

    if fields:
        logger.info("Extracted %d form fields from PDF", len(fields))
    return fields


def _reconstruct_document(
    extract_json: dict[str, Any],
    pdf_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct full document structure from Adobe Extract JSON.

    Improvements over the basic approach:
    - Pre-merges single-character fragment elements with their neighbors
    - Detects list structures from Adobe paths (/L/LI/LBody), skips /Lbl labels
    - Cleans cross-reference artifacts (<>) from text
    - Fixes broken words conservatively (suffix-based, not aggressive merging)
    - Embeds real image data (base64) for figures when pdf_path is provided
    - Uses Adobe filePaths for deterministic figure-to-image mapping (preferred)
    - Falls back to PyMuPDF bounding-box matching when Adobe figures unavailable
    - Provides descriptive alt text placeholders for figures
    - Accumulates consecutive list items into proper list elements
    - Infers true heading levels from TextSize font metadata (H1-H6)
    """
    raw_elements = extract_json.get("elements", [])
    if not raw_elements:
        return []

    # Infer heading levels from font size BEFORE fragment merging --
    # we want ALL heading elements (including TOC) for accurate size collection.
    heading_level_map = _infer_heading_levels(raw_elements)
    if heading_level_map:
        logger.info(
            "Heading level inference: %d distinct sizes -> %s",
            len(heading_level_map),
            {str(k) + "pt": f"H{v}" for k, v in sorted(heading_level_map.items(), reverse=True)},
        )

    # Adobe filePaths figure images — deterministic mapping from the Extract ZIP.
    # Preferred over PyMuPDF because Adobe's renditions are composited figures,
    # not individual embedded raster streams.
    adobe_figure_images: dict[str, str] = extract_json.get("_figure_images", {})
    if adobe_figure_images:
        logger.info(
            "_reconstruct_document: %d Adobe figure images available via filePaths",
            len(adobe_figure_images),
        )

    # Fallback: extract images from the PDF using PyMuPDF for bounding-box matching.
    # Only runs when Adobe figures are NOT available and a local pdf_path exists.
    extracted_images: list[dict[str, Any]] = []
    if not adobe_figure_images and pdf_path is not None:
        extracted_images = _extract_images_from_pdf(pdf_path)
        if extracted_images:
            logger.info(
                "_reconstruct_document: extracted %d images from PDF via PyMuPDF (fallback)",
                len(extracted_images),
            )

    # Pre-pass: merge single-character fragment elements
    raw_elements = _merge_fragment_elements(raw_elements)

    # Identify table ranges
    table_ranges = _identify_table_ranges(raw_elements)
    table_outputs = _build_tables(raw_elements, table_ranges)

    # Track consumed indices
    table_consumed: set[int] = set()
    table_insert_at: dict[int, list[dict[str, Any]]] = {}
    for (start, end), outputs in zip(table_ranges, table_outputs):
        for i in range(start, end + 1):
            table_consumed.add(i)
        if outputs:
            table_insert_at[start] = outputs

    result: list[dict[str, Any]] = []
    # Accumulator for consecutive list items
    pending_list_items: list[str] = []
    pending_list_ordered: bool = False
    pending_list_page: int = 0

    def _flush_list() -> None:
        """Emit accumulated list items as a single list element."""
        nonlocal pending_list_page
        if not pending_list_items:
            return
        result.append({
            "type": "list",
            "content": "",
            "attributes": {
                "items": list(pending_list_items),
                "ordered": pending_list_ordered,
                "page": pending_list_page,
            },
        })
        pending_list_items.clear()
        pending_list_page = 0

    skip_indices: set[int] = set()
    filtered_artifact_count = 0

    for i, elem in enumerate(raw_elements):
        if i in skip_indices:
            continue
        if i in table_insert_at:
            _flush_list()
            result.extend(table_insert_at[i])
        if i in table_consumed:
            continue

        # --- Skip header/footer/pagination artifacts (WCAG 1.3.1, 1.3.2) ---
        if _is_artifact(elem):
            filtered_artifact_count += 1
            continue

        path = elem.get("Path", "")
        text = _clean_text(elem.get("Text") or "")

        # --- Skip TOC elements (content is duplicated in body headings) ---
        if _RE_TOC.search(path):
            continue

        # --- Footnotes → render as small paragraphs ---
        if _RE_FOOTNOTE.search(path) and text:
            _flush_list()
            fn_attrs: dict[str, Any] = {"role": "note", "page": elem.get("Page", 0)}
            fn_style = _extract_element_style(elem)
            if fn_style:
                fn_attrs["style"] = fn_style
            result.append({"type": "paragraph", "content": text, "attributes": fn_attrs})
            continue

        # --- Headings ---
        hm = _RE_HEADING.search(path)
        if hm and text:
            _flush_list()
            # Determine heading level: prefer inferred level from TextSize metadata
            # (Adobe marks all headings as H1 regardless of visual hierarchy).
            # Fall back to the level encoded in the Path (/H1, /H2, etc.) when
            # no TextSize data is available (e.g. pypdf fallback).
            path_level = int(hm.group(1))
            if heading_level_map:
                raw_size = elem.get("TextSize")
                if raw_size is not None:
                    try:
                        rounded_size = round(float(raw_size), 1)
                        inferred_level = heading_level_map.get(rounded_size, path_level)
                    except (TypeError, ValueError):
                        inferred_level = path_level
                else:
                    inferred_level = path_level
            else:
                inferred_level = path_level

            # NOTE: _numbering_depth() override was removed because it conflicts
            # with TextSize-based hierarchy.  Heading levels are now determined
            # solely by font size (via _infer_heading_levels) and post-processed
            # by _enforce_heading_hierarchy() to guarantee a valid tree.

            h_attrs: dict[str, Any] = {"level": inferred_level, "page": elem.get("Page", 0)}
            h_style = _extract_element_style(elem)
            if h_style:
                h_attrs["style"] = h_style
            result.append({"type": "heading", "content": text, "attributes": h_attrs})
            continue

        # --- Figures / Images ---
        if _RE_FIGURE.search(path):
            _flush_list()
            # Prefer Adobe's alternate_text field, then Alt attribute, then placeholder
            alt = (
                elem.get("alternate_text", "")
                or elem.get("Alt", "")
                or (elem.get("attributes") or {}).get("Alt", "")
            )
            if not alt:
                # Descriptive placeholder that signals AI drafting is needed
                page_num = elem.get("Page", "?")
                alt = f"[Figure on page {page_num} — alt text requires review]"
                needs_review = True
            else:
                needs_review = False

            img_attrs: dict[str, Any] = {"alt": alt, "page": elem.get("Page", 0)}
            if needs_review:
                img_attrs["data-needs-review"] = "alt-text"

            # Strategy 1 (preferred): Use Adobe filePaths — deterministic mapping
            # from the Extract ZIP.  The element's filePaths field (e.g.
            # ["figures/fileoutpart0.png"]) maps directly to the rendered figure.
            adobe_src = None
            file_paths = elem.get("filePaths") or elem.get("FilePaths") or []
            if file_paths and adobe_figure_images:
                for fp in file_paths:
                    if fp in adobe_figure_images:
                        adobe_src = adobe_figure_images[fp]
                        break

            if adobe_src:
                img_attrs["src"] = adobe_src
            else:
                # Strategy 2 (fallback): PyMuPDF bounding-box matching
                matched = _match_figure_to_image(elem, extracted_images)
                if matched:
                    data_uri = f"data:{matched['mime']};base64,{matched['base64']}"
                    img_attrs["src"] = data_uri
                    extracted_images.remove(matched)  # prevent duplicate assignment
                elif pdf_path is not None:
                    # Strategy 2b: clip the figure region from the original PDF
                    clipped = _clip_figure_from_pdf(
                        pdf_path, elem.get("Page", 0), elem.get("Bounds"),
                    )
                    if clipped:
                        img_attrs["src"] = clipped
                        logger.info(
                            "Figure on page %s: recovered via bbox clip (alt=%s)",
                            elem.get("Page", "?"), alt[:60],
                        )

                if "src" not in img_attrs:
                    # Strategy 3: transparent placeholder — keeps the element
                    # in the document with descriptive alt text (still accessible)
                    # rather than omitting it entirely or leaving src empty.
                    _placeholder_svg = (
                        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
                        '<rect width="100%" height="100%" fill="#f3f4f6"/>'
                        '<text x="50%" y="50%" text-anchor="middle" fill="#6b7280" '
                        'font-family="sans-serif" font-size="14">'
                        '[Figure - see alt text]</text></svg>'
                    )
                    img_attrs["src"] = (
                        "data:image/svg+xml;base64,"
                        + base64.b64encode(_placeholder_svg.encode("utf-8")).decode()
                    )
                    img_attrs["data-placeholder"] = "true"
                    logger.info(
                        "Figure on page %s: no image data found, using placeholder (alt=%s)",
                        elem.get("Page", "?"), alt[:60],
                    )

            result.append({"type": "image", "content": "", "attributes": img_attrs})

            # Look ahead: associate a figure caption from the next element
            if i + 1 < len(raw_elements):
                next_elem = raw_elements[i + 1]
                next_text = _clean_text(next_elem.get("Text") or "")
                if next_text and re.match(r"(?i)(?:FIGURE|Fig\.?)\s+\d+", next_text):
                    result[-1]["attributes"]["caption"] = next_text
                    skip_indices.add(i + 1)

            continue

        if not text:
            continue

        # --- Skip list label elements (bullet markers like "•", "1.") ---
        if _RE_LIST_LABEL.search(path):
            continue

        # --- List items (from Adobe path OR bullet/number prefix) ---
        is_list_path = bool(_RE_LIST.search(path))
        bullet_match = _RE_BULLET.match(text)
        numbered_match = _RE_NUMBERED.match(text)

        if is_list_path or bullet_match or numbered_match:
            # Strip the bullet/number prefix for clean list item text
            if bullet_match:
                item_text = text[bullet_match.end():]
                is_ordered = False
            elif numbered_match:
                item_text = text[numbered_match.end():]
                is_ordered = True
            else:
                item_text = text
                is_ordered = False

            item_text = item_text.strip()
            if item_text:
                # If switching between ordered/unordered, flush first
                if pending_list_items and pending_list_ordered != is_ordered:
                    _flush_list()
                if not pending_list_items:
                    pending_list_page = elem.get("Page", 0)
                pending_list_ordered = is_ordered
                pending_list_items.append(item_text)
            continue

        # --- Regular paragraph ---
        _flush_list()
        p_attrs: dict[str, Any] = {"page": elem.get("Page", 0)}
        p_style = _extract_element_style(elem)
        if p_style:
            p_attrs["style"] = p_style
        result.append({"type": "paragraph", "content": text, "attributes": p_attrs})

    # Flush any remaining list items
    _flush_list()

    # Post-pass: enforce valid heading hierarchy (single H1, monotonic, no skips)
    _enforce_heading_hierarchy(result)

    # Post-pass: detect "TABLE N:" paragraphs and convert to table captions
    result = _attach_table_captions(result)

    logger.info(
        "Reconstructed: %d raw -> %d output (%d table regions, %d artifacts filtered)",
        len(raw_elements), len(result), len(table_ranges), filtered_artifact_count,
    )
    return result


def _identify_table_ranges(elements: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Identify index ranges for each table."""
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(elements):
        path = elements[i].get("Path", "")
        tm = _RE_TABLE_BASE.match(path)
        if tm and "/Table" in path:
            base = tm.group(1)
            start = i
            end = i
            j = i + 1
            while j < len(elements):
                if elements[j].get("Path", "").startswith(base):
                    end = j
                    j += 1
                else:
                    break
            ranges.append((start, end))
            i = end + 1
        else:
            i += 1
    return ranges


# ---------------------------------------------------------------------------
# Table reconstruction — split by page
# ---------------------------------------------------------------------------

def _build_tables(
    elements: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
) -> list[list[dict[str, Any]]]:
    """Build table + heading elements from each table range.

    For multi-page tables, splits into separate tables per page to
    produce one accessible table per zone/section.
    """
    all_outputs: list[list[dict[str, Any]]] = []

    for start, end in ranges:
        # Collect text cells: (page, row_idx, col_sort_key, cell_type, text)
        raw_cells: list[tuple[int, int, int, str, str]] = []

        for i in range(start, end + 1):
            elem = elements[i]
            text = _clean_text(elem.get("Text") or "")
            if not text:
                continue
            path = elem.get("Path", "")
            page = elem.get("Page", 0)

            cm = _RE_TABLE_CELL.search(path)
            if not cm:
                continue

            row_idx = int(cm.group(1)) if cm.group(1) else 0
            cell_type = cm.group(2)  # TH or TD
            col_idx = int(cm.group(3)) if cm.group(3) else 0

            # Sort key: TH before TD at same position, then by col index
            sort_key = col_idx * 2 + (0 if cell_type == "TH" else 1)
            raw_cells.append((page, row_idx, sort_key, cell_type, text))

        if not raw_cells:
            all_outputs.append([])
            continue

        # Merge multi-line text within the same cell (same page+row+col)
        # Adobe sometimes splits one cell's text into multiple elements
        merged: dict[tuple[int, int, int], tuple[str, list[str]]] = {}
        for page, row_idx, sort_key, cell_type, text in raw_cells:
            key = (page, row_idx, sort_key)
            if key in merged:
                merged[key][1].append(text)
            else:
                merged[key] = (cell_type, [text])

        cells: list[tuple[int, int, int, str, str]] = []
        for (page, row_idx, sort_key), (cell_type, texts) in merged.items():
            combined = " ".join(texts)
            cells.append((page, row_idx, sort_key, cell_type, combined))

        # Group by page
        pages = sorted(set(c[0] for c in cells))
        output_elems: list[dict[str, Any]] = []

        for page in pages:
            page_cells = [(r, sk, ct, t) for p, r, sk, ct, t in cells if p == page]
            page_output = _build_single_page_table(page_cells, page_num=page)
            output_elems.extend(page_output)

        all_outputs.append(output_elems)

    return all_outputs


def _build_single_page_table(
    cells: list[tuple[int, int, str, str]],
    page_num: int = 0,
) -> list[dict[str, Any]]:
    """Build heading + table elements for a single page's worth of table data.

    cells: list of (row_idx, sort_key, cell_type, text)

    Layout pattern (Sacramento fee schedules):
    - Rows 0-4: title/subtitle (APPENDIX 1, DRAINAGE FEE SCHEDULE, ZONE 11A)
    - Row 5 or 6: column headers (LAND USE, Zone Fee, Pre-2004 Fee, ...)
    - Rows 7+: data rows (land use in TH, fees in TD)
    - Rows 40+: footnotes ([1], [2], ...)
    """
    if not cells:
        return []

    # Group by row
    rows_map: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for row_idx, sort_key, cell_type, text in cells:
        rows_map[row_idx].append((sort_key, cell_type, text))

    # Sort cells within each row
    for row_idx in rows_map:
        rows_map[row_idx].sort(key=lambda c: c[0])

    sorted_rows = sorted(rows_map.keys())

    # Find the column header row using multiple heuristics (priority order):
    # 1. First row that is entirely TH cells
    # 2. First row that mixes TH + TD cells
    # 3. First row where all text looks like column labels (short, no numbers/currency)
    # 4. Last fallback: first row with 2+ cells
    header_row_idx: int | None = None

    # Heuristic 1: Row of all TH cells
    for row_idx in sorted_rows:
        types = set(ct for _, ct, _ in rows_map[row_idx])
        if types == {"TH"} and len(rows_map[row_idx]) >= 2:
            header_row_idx = row_idx
            break

    # Heuristic 2: Row mixing TH + TD (Adobe sometimes marks header cells as TH)
    if header_row_idx is None:
        for row_idx in sorted_rows:
            types = set(ct for _, ct, _ in rows_map[row_idx])
            if "TH" in types and "TD" in types:
                header_row_idx = row_idx
                break

    # Heuristic 3: First multi-cell row where texts look like column labels
    # (no currency symbols, no pure numbers, mostly short text)
    if header_row_idx is None:
        _currency_re = re.compile(r"^[\$\€\£]?\s*[\d,]+\.?\d*\s*%?$")
        for row_idx in sorted_rows:
            row_cells = rows_map[row_idx]
            if len(row_cells) < 2:
                continue
            texts = [t for _, _, t in row_cells]
            # Skip if most cells are numeric/currency values
            numeric_count = sum(1 for t in texts if _currency_re.match(t.strip()))
            if numeric_count > len(texts) / 2:
                continue
            # Good candidate: multi-cell row with mostly text labels
            header_row_idx = row_idx
            break

    # Heuristic 4: Last fallback — first row with 2+ cells
    if header_row_idx is None:
        for row_idx in sorted_rows:
            if len(rows_map[row_idx]) >= 2:
                header_row_idx = row_idx
                break

    if header_row_idx is None:
        # All single-cell rows — use the first row as header instead of
        # collapsing to paragraphs, so we still emit a proper table element.
        header_row_idx = sorted_rows[0]

    output: list[dict[str, Any]] = []

    # Check if the row immediately before the header row has TD cells
    # with column names (e.g., "March 2025 Zone 11A Fee for LAGUNA WEST...")
    # If so, those TD column names should be merged into the header row.
    extra_header_row_idx: int | None = None
    prev_row_idx = None
    for row_idx in sorted_rows:
        if row_idx == header_row_idx:
            break
        prev_row_idx = row_idx

    if prev_row_idx is not None:
        prev_cells = rows_map[prev_row_idx]
        has_td_text = any(ct == "TD" for _, ct, _ in prev_cells)
        # If previous row has TD cells (not just title TH cells), it's a header extension
        if has_td_text:
            extra_header_row_idx = prev_row_idx

    # Pre-header rows → headings (excluding extra header row)
    # Detect "Table N:" pattern for <caption>
    _RE_TABLE_CAPTION = re.compile(
        r"^(Table\s+\d+[A-Za-z]?\s*[-:.]\s*.+)$", re.IGNORECASE
    )
    caption_text: str = ""
    for row_idx in sorted_rows:
        if row_idx >= header_row_idx:
            break
        if row_idx == extra_header_row_idx:
            continue
        texts = [t for _, _, t in rows_map[row_idx]]
        combined = " — ".join(t for t in texts if t.strip())
        if not combined.strip():
            continue
        # Check if this row matches "Table N: description"
        cap_match = _RE_TABLE_CAPTION.match(combined.strip())
        if cap_match and not caption_text:
            caption_text = cap_match.group(1).strip()
        else:
            output.append({
                "type": "heading",
                "content": combined.strip(),
                "attributes": {"level": 2, "page": page_num},
            })

    # Build column headers: start with the LAND USE row,
    # then incorporate extra column names from the row before it
    header_cells = rows_map[header_row_idx]
    headers = [t for _, _, t in header_cells]

    if extra_header_row_idx is not None:
        extra_cells = rows_map[extra_header_row_idx]
        extra_td_texts = [t for _, ct, t in extra_cells if ct == "TD"]
        # Append extra column names after "LAND USE"
        headers.extend(extra_td_texts)

    # --- First pass: collect all candidate data rows to determine table shape ---
    candidate_rows: list[tuple[int, list[str]]] = []  # (row_idx, texts)
    footnotes: list[str] = []
    in_footnotes = False
    expected_cols = len(headers)

    for row_idx in sorted_rows:
        if row_idx <= header_row_idx:
            continue

        row_cells = rows_map[row_idx]
        row_texts = [t for _, _, t in row_cells]

        # Check for "Equation" rows (interleaved between data rows) — skip
        if len(row_texts) == 1 and row_texts[0].lower().startswith("equation"):
            continue

        # Check for footnote rows (start with [N])
        first = row_texts[0] if row_texts else ""
        if first.startswith("[") and re.match(r"^\[\d+\]", first):
            in_footnotes = True
            footnotes.append(" ".join(row_texts))
            continue

        # Check for "Source:" / "Note:" attribution rows → always footnote
        if len(row_texts) == 1 and re.match(r"^(?:Source|Note|Notes)\s*:", first, re.IGNORECASE):
            in_footnotes = True
            footnotes.append(first)
            continue

        # Once in footnote territory, continuation lines are also footnotes
        if in_footnotes:
            footnotes.append(" ".join(row_texts))
            continue

        candidate_rows.append((row_idx, row_texts))

    # --- Determine if this is a "form-like" table (most rows have 1 cell) ---
    # vs a "data table" (most rows have multiple cells matching header count)
    multi_cell_count = sum(1 for _, texts in candidate_rows if len(texts) >= 2)
    single_cell_count = sum(1 for _, texts in candidate_rows if len(texts) == 1)
    is_form_table = (expected_cols >= 2 and multi_cell_count == 0 and single_cell_count > 0)

    # --- Second pass: classify rows based on table type ---
    data_rows: list[list[str]] = []
    trailing_paragraphs: list[str] = []
    seen_multi_cell_data = False

    for _row_idx, row_texts in candidate_rows:
        if is_form_table:
            # Form table: keep all rows as-is (single-cell rows ARE the data)
            data_rows.append(row_texts)
            continue

        if expected_cols >= 2 and len(row_texts) == 1:
            if seen_multi_cell_data:
                # After real data rows, single-cell = trailing footnote/note
                footnotes.append(row_texts[0])
            else:
                # Before real data, single-cell = section label
                trailing_paragraphs.append(row_texts[0])
            continue

        if len(row_texts) >= 2:
            seen_multi_cell_data = True

        # Don't pad here — let the HTML renderer handle column normalization
        data_rows.append(row_texts)

    # Build table element
    if headers or data_rows:
        output.append({
            "type": "table",
            "content": caption_text,
            "attributes": {"headers": headers, "rows": data_rows, "page": page_num},
        })

    # Trailing paragraphs (labels that came before data in multi-col tables)
    for tp in trailing_paragraphs:
        output.append({"type": "paragraph", "content": tp, "attributes": {"page": page_num}})

    # Footnotes as paragraphs
    for fn in footnotes:
        output.append({"type": "paragraph", "content": fn, "attributes": {"page": page_num}})

    return output
