"""CLI entry point for the WCAG PDF remediation pipeline.

Usage:
    python -m app.cli --input <pdf_or_folder> --outdir <dir> [--watch] [--format html|pdf|both]

Examples:
    # Single PDF
    python -m app.cli --input doc.pdf --outdir output/

    # Folder of PDFs
    python -m app.cli --input /path/to/pdfs/ --outdir output/

    # Watch mode (polls for new PDFs)
    python -m app.cli --input /path/to/pdfs/ --outdir output/ --watch

    # Generate both HTML and PDF
    python -m app.cli --input doc.pdf --outdir output/ --format both
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def process_single_pdf(
    pdf_path: Path,
    outdir: Path,
    fmt: str,
) -> dict:
    """Process a single PDF and write structured output.

    Returns a result dict with status information.
    """
    from app.output import OutputBuilder
    from services.ingestion.converter import (
        stage_build_html,
        stage_extract,
        stage_output,
        stage_validate,
    )

    stem = pdf_path.stem
    logger.info("Processing: %s", pdf_path.name)
    start_time = time.monotonic()

    ob = OutputBuilder(outdir, stem)
    pdf_bytes = pdf_path.read_bytes()

    # Stage 1+2: Extract → IR
    ir_doc = stage_extract(pdf_bytes, pdf_path.name)
    ob.write_ir(ir_doc)

    # Stage 3: IR → HTML
    title = f"{stem} — WCAG Remediated"
    html_content, builder = stage_build_html(ir_doc, title)

    # Stage 4: Validate
    validation = stage_validate(builder, html_content)

    # Stage 5: Output
    if fmt in ("html", "both"):
        html_bytes, _ = stage_output(html_content, "html", builder)
        ob.write_html(html_bytes)

    if fmt in ("pdf", "both"):
        pdf_out_bytes, _ = stage_output(html_content, "pdf", builder)
        ob.write_pdf(pdf_out_bytes)

    # Write stub reports (populated by gates in Phase B)
    ob.write_validation_ledger({
        "document_id": ir_doc.document_id,
        "filename": pdf_path.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gates": [],
        "summary": {
            "total_checks": validation.get("total_checks", 0),
            "passed": validation.get("checks_passed", 0),
            "soft_fails": 0,
            "hard_fails": len(validation.get("violations", [])),
            "overall": "pass" if validation.get("score", 0) >= 0.8 else "fail",
        },
    })

    ob.write_review_needed({
        "document_id": ir_doc.document_id,
        "filename": pdf_path.name,
        "items": [],
        "summary": {"total_items": 0, "by_category": {}, "by_severity": {}},
    })

    ob.write_doc_profile({
        "filename": pdf_path.name,
        "page_count": ir_doc.page_count,
        "pages": [],
        "overall_route": "all_adobe",
    })

    elapsed = time.monotonic() - start_time
    result = {
        "filename": pdf_path.name,
        "status": "ok",
        "elapsed_s": round(elapsed, 2),
        "output_dir": str(ob.doc_dir),
        "score": validation.get("score", 0),
        "violations": len(validation.get("violations", [])),
        "ir_blocks": len(ir_doc.all_blocks()),
        "ir_pages": ir_doc.page_count,
    }

    logger.info(
        "Done: %s — score=%.2f, %d blocks, %.1fs",
        pdf_path.name, result["score"], result["ir_blocks"], elapsed,
    )
    return result


def watch_mode(
    input_dir: Path,
    outdir: Path,
    fmt: str,
    poll_interval: float = 5.0,
) -> None:
    """Poll input_dir for new PDFs and process them."""
    processed: set[str] = set()
    logger.info("Watch mode: polling %s every %.1fs", input_dir, poll_interval)

    try:
        while True:
            for pdf_path in sorted(input_dir.glob("*.pdf")):
                if pdf_path.name not in processed:
                    try:
                        result = process_single_pdf(pdf_path, outdir, fmt)
                        print(json.dumps(result, indent=2))
                    except Exception as exc:
                        logger.error("Failed: %s — %s", pdf_path.name, exc)
                        print(json.dumps({
                            "filename": pdf_path.name,
                            "status": "error",
                            "error": str(exc),
                        }, indent=2))
                    processed.add(pdf_path.name)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Watch mode stopped by user")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WCAG PDF Remediation Pipeline — process PDFs into accessible HTML/PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m app.cli --input doc.pdf --outdir output/\n"
            "  python -m app.cli --input /path/to/pdfs/ --outdir output/ --format both\n"
            "  python -m app.cli --input /path/to/pdfs/ --outdir output/ --watch\n"
        ),
    )
    parser.add_argument(
        "--input", required=True,
        help="PDF file or folder of PDFs to process",
    )
    parser.add_argument(
        "--outdir", required=True,
        help="Output directory for structured results",
    )
    parser.add_argument(
        "--format", default="html", choices=["html", "pdf", "both"],
        help="Output format (default: html)",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch mode: poll input folder for new PDFs",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.watch:
        if not input_path.is_dir():
            print("Error: --watch requires --input to be a directory", file=sys.stderr)
            sys.exit(1)
        watch_mode(input_path, outdir, args.format)
    elif input_path.is_file() and input_path.suffix.lower() == ".pdf":
        result = process_single_pdf(input_path, outdir, args.format)
        print(json.dumps(result, indent=2))
    elif input_path.is_dir():
        pdfs = sorted(input_path.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF files found in: {input_path}", file=sys.stderr)
            sys.exit(1)

        results = []
        for pdf in pdfs:
            try:
                result = process_single_pdf(pdf, outdir, args.format)
                results.append(result)
            except Exception as exc:
                logger.error("Failed: %s — %s", pdf.name, exc)
                results.append({
                    "filename": pdf.name,
                    "status": "error",
                    "error": str(exc),
                })

        # Summary
        ok = sum(1 for r in results if r["status"] == "ok")
        err = sum(1 for r in results if r["status"] == "error")
        print(json.dumps({
            "total": len(results),
            "ok": ok,
            "errors": err,
            "results": results,
        }, indent=2))
    else:
        print(f"Input not found or not a PDF: {input_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
