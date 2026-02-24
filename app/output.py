"""Structured output directory builder for the WCAG pipeline.

Creates the per-document output directory structure:
    outdir/<doc_name>/
        html/index.html
        accessibility/remediated.pdf
        reports/doc_profile.json
        reports/validation_ledger.json
        reports/review_needed.json
        reports/ir.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.common.ir import IRDocument

logger = logging.getLogger(__name__)


class OutputBuilder:
    """Builds the structured output directory for a single document."""

    def __init__(self, outdir: Path, doc_stem: str) -> None:
        self.doc_dir = outdir / doc_stem
        self.html_dir = self.doc_dir / "html"
        self.accessibility_dir = self.doc_dir / "accessibility"
        self.reports_dir = self.doc_dir / "reports"

        for d in (self.html_dir, self.accessibility_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def write_html(self, html_bytes: bytes) -> Path:
        """Write the remediated HTML file."""
        path = self.html_dir / "index.html"
        path.write_bytes(html_bytes)
        logger.info("Wrote HTML: %s (%d bytes)", path, len(html_bytes))
        return path

    def write_pdf(self, pdf_bytes: bytes) -> Path:
        """Write the remediated PDF/UA file."""
        path = self.accessibility_dir / "remediated.pdf"
        path.write_bytes(pdf_bytes)
        logger.info("Wrote PDF: %s (%d bytes)", path, len(pdf_bytes))
        return path

    def write_ir(self, ir_doc: IRDocument) -> Path:
        """Write the IR document as JSON."""
        path = self.reports_dir / "ir.json"
        path.write_text(ir_doc.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Wrote IR: %s", path)
        return path

    def write_report(self, name: str, data: dict[str, Any]) -> Path:
        """Write a named JSON report (doc_profile, validation_ledger, etc.)."""
        path = self.reports_dir / name
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info("Wrote report: %s", path)
        return path

    def write_validation_ledger(self, ledger: dict[str, Any]) -> Path:
        return self.write_report("validation_ledger.json", ledger)

    def write_review_needed(self, review: dict[str, Any]) -> Path:
        return self.write_report("review_needed.json", review)

    def write_doc_profile(self, profile: dict[str, Any]) -> Path:
        return self.write_report("doc_profile.json", profile)
