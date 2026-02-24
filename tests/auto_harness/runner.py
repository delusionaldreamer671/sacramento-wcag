"""Auto test harness for WCAG remediation pipeline.

Runs a corpus of PDFs through the conversion pipeline, validates
the HTML output, classifies gaps, and produces a structured report.

Usage:
    python -m tests.auto_harness.runner "C:\\path\\to\\test-corpus"

The harness calls convert_pdf_sync() directly — no server process needed.
Results are written to:
    C:\\Users\\sahaj\\sacramento-wcag\\test-results\\
        <timestamp>_gap_report.json   — full JSON report
        <pdf-stem>.html               — per-document HTML output
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging — configure before any project imports so converter output is
# visible in the terminal during the run.
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("harness.runner")

# ---------------------------------------------------------------------------
# Project imports (after logging is live)
# ---------------------------------------------------------------------------

from services.ingestion.converter import convert_pdf_sync  # noqa: E402

try:
    from tests.auto_harness.html_validator import validate_html
    _VALIDATOR_AVAILABLE = True
except ImportError:
    logger.warning(
        "tests.auto_harness.html_validator not found — "
        "validation step will be skipped for all documents."
    )
    _VALIDATOR_AVAILABLE = False
    validate_html = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESULTS_DIR = Path("C:/Users/sahaj/sacramento-wcag/test-results")

# Classification thresholds — used when html_validator is unavailable and
# we fall back to the pipeline's own accessibility score.
_SCORE_GREEN = 0.90   # score >= 0.90 → GREEN (auto-pass)
_SCORE_YELLOW = 0.70  # score >= 0.70 → YELLOW (needs review)
# below 0.70 → RED (needs human attention)


# ---------------------------------------------------------------------------
# Per-document processing
# ---------------------------------------------------------------------------


def _classify_score(score: float) -> str:
    """Map a 0–1 accessibility score to GREEN / YELLOW / RED."""
    if score >= _SCORE_GREEN:
        return "GREEN"
    if score >= _SCORE_YELLOW:
        return "YELLOW"
    return "RED"


def _process_one(pdf_path: Path, output_dir: Path) -> dict:
    """Convert a single PDF and validate the HTML output.

    Returns a result dict with keys:
        filename, status, elapsed_s, html_path,
        score, classification, gaps, error
    """
    result: dict = {
        "filename": pdf_path.name,
        "status": "ok",
        "elapsed_s": 0.0,
        "html_path": None,
        "score": None,
        "classification": None,
        "gaps": [],
        "error": None,
    }

    # --- Conversion ---
    t0 = time.perf_counter()
    try:
        pdf_bytes = pdf_path.read_bytes()
        html_bytes, _content_type = convert_pdf_sync(pdf_bytes, pdf_path.name, "html")
        elapsed = time.perf_counter() - t0
        result["elapsed_s"] = round(elapsed, 3)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        result["elapsed_s"] = round(elapsed, 3)
        result["status"] = "conversion_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("Conversion FAILED for %s: %s", pdf_path.name, exc)
        return result

    html_content = html_bytes.decode("utf-8", errors="replace")

    # --- Save HTML output ---
    html_out = output_dir / (pdf_path.stem + ".html")
    try:
        html_out.write_text(html_content, encoding="utf-8")
        result["html_path"] = str(html_out)
    except Exception as exc:
        logger.warning("Could not save HTML for %s: %s", pdf_path.name, exc)

    # --- Validation ---
    if _VALIDATOR_AVAILABLE and validate_html is not None:
        try:
            validation = validate_html(html_content, pdf_path.name)
            # html_validator.validate_html() returns:
            #   total_checks, passed, failed, gaps (list[dict]), score (0-100%)
            # Normalize score to 0-1 range and derive classification
            raw_score = validation.get("score", 0.0)
            score_01 = raw_score / 100.0 if raw_score > 1.0 else raw_score
            result["score"] = score_01
            result["classification"] = _classify_score(score_01)
            # Normalize gap format: check_id → criterion
            result["gaps"] = [
                {
                    "criterion": g.get("check_id", g.get("criterion", "unknown")),
                    "severity": g.get("severity", "unknown"),
                    "description": g.get("description", ""),
                    "classification": g.get("classification", "YELLOW"),
                    "details": g.get("details", {}),
                }
                for g in validation.get("gaps", [])
            ]
        except Exception as exc:
            logger.warning("Validator raised for %s: %s", pdf_path.name, exc)
            result["status"] = "validation_error"
            result["error"] = f"Validator: {type(exc).__name__}: {exc}"
    else:
        # Fallback: use the pipeline's built-in accessibility score embedded
        # in the HTML as a meta tag, or re-run validate_accessibility directly.
        try:
            from services.recompilation.pdfua_builder import PDFUABuilder
            import uuid

            builder = PDFUABuilder(
                document_id=str(uuid.uuid4()),
                document_title=pdf_path.stem,
            )
            validation = builder.validate_accessibility(html_content)
            score = validation.get("score", 0.0)
            violations = validation.get("violations", [])
            classification = _classify_score(score)

            result["score"] = score
            result["classification"] = classification
            result["gaps"] = [
                {
                    "criterion": v.get("criterion", "unknown"),
                    "severity": v.get("severity", "unknown"),
                    "description": v.get("description", ""),
                    "classification": (
                        "RED"
                        if v.get("severity") in ("critical", "serious")
                        else "YELLOW"
                    ),
                }
                for v in violations
            ]
        except Exception as exc:
            logger.warning("Fallback validation failed for %s: %s", pdf_path.name, exc)
            result["score"] = None
            result["classification"] = "UNKNOWN"

    logger.info(
        "%-40s  %s  score=%-6s  elapsed=%.2fs  gaps=%d",
        pdf_path.name,
        result.get("classification", "UNKNOWN"),
        f"{result['score']:.3f}" if result["score"] is not None else "n/a",
        result["elapsed_s"],
        len(result["gaps"]),
    )
    return result


# ---------------------------------------------------------------------------
# Corpus runner
# ---------------------------------------------------------------------------


def run_corpus(corpus_dir: Path) -> dict:
    """Process all PDFs in corpus_dir, return aggregate results.

    Returns:
        dict with keys:
            timestamp, corpus_path, results (list), stats (dict),
            gap_summary (dict: GREEN/YELLOW/RED counts and top gap types)
    """
    pdf_files = sorted(corpus_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No .pdf files found in %s", corpus_dir)

    logger.info("Found %d PDF(s) in %s", len(pdf_files), corpus_dir)

    # Output directory
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    per_doc: list[dict] = []
    for pdf_path in pdf_files:
        logger.info("--- Processing: %s ---", pdf_path.name)
        doc_result = _process_one(pdf_path, RESULTS_DIR)
        per_doc.append(doc_result)

    # --- Aggregate statistics ---
    total = len(per_doc)
    ok_count = sum(1 for r in per_doc if r["status"] == "ok")
    error_count = sum(1 for r in per_doc if r["status"] not in ("ok", "validation_error"))
    validation_error_count = sum(1 for r in per_doc if r["status"] == "validation_error")

    classification_counts: dict[str, int] = {"GREEN": 0, "YELLOW": 0, "RED": 0, "UNKNOWN": 0}
    for r in per_doc:
        c = r.get("classification") or "UNKNOWN"
        classification_counts[c] = classification_counts.get(c, 0) + 1

    # Gap type frequency
    gap_type_freq: dict[str, int] = {}
    red_items: list[dict] = []
    for r in per_doc:
        for gap in r.get("gaps", []):
            crit = gap.get("criterion", "unknown")
            gap_type_freq[crit] = gap_type_freq.get(crit, 0) + 1
            if gap.get("classification") == "RED" or gap.get("severity") in ("critical", "serious"):
                red_items.append({"file": r["filename"], **gap})

    top_gap_types = sorted(gap_type_freq.items(), key=lambda kv: kv[1], reverse=True)[:10]

    pass_rate = (ok_count / total) if total > 0 else 0.0
    green_rate = (
        classification_counts["GREEN"] / total
        if total > 0
        else 0.0
    )

    scores = [r["score"] for r in per_doc if r["score"] is not None]
    avg_score = sum(scores) / len(scores) if scores else None

    timings = [r["elapsed_s"] for r in per_doc if r["elapsed_s"]]
    avg_elapsed = sum(timings) / len(timings) if timings else 0.0
    total_elapsed = sum(timings)

    stats = {
        "total_pdfs": total,
        "conversion_ok": ok_count,
        "conversion_errors": error_count,
        "validation_errors": validation_error_count,
        "pass_rate": round(pass_rate, 4),
        "green_rate": round(green_rate, 4),
        "avg_accessibility_score": round(avg_score, 4) if avg_score is not None else None,
        "avg_elapsed_s": round(avg_elapsed, 3),
        "total_elapsed_s": round(total_elapsed, 3),
        "validator_available": _VALIDATOR_AVAILABLE,
    }

    gap_summary = {
        "classification_counts": classification_counts,
        "top_gap_types": [{"criterion": k, "count": v} for k, v in top_gap_types],
        "red_items_requiring_attention": red_items,
    }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus_path": str(corpus_dir),
        "results": per_doc,
        "stats": stats,
        "gap_summary": gap_summary,
    }


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------


def print_summary(results: dict) -> None:
    """Print a formatted summary table to terminal."""
    stats = results["stats"]
    gap_summary = results["gap_summary"]
    docs = results["results"]
    counts = gap_summary["classification_counts"]

    bar = "=" * 72
    thin = "-" * 72

    print()
    print(bar)
    print("  WCAG REMEDIATION PIPELINE — TEST HARNESS RESULTS")
    print(f"  Corpus : {results['corpus_path']}")
    print(f"  Run at : {results['timestamp']}")
    print(bar)
    print()

    # Per-document table
    col_w = [38, 8, 7, 8, 6]
    header = (
        f"{'FILE':<{col_w[0]}}  "
        f"{'STATUS':<{col_w[1]}}  "
        f"{'SCORE':<{col_w[2]}}  "
        f"{'CLASS':<{col_w[3]}}  "
        f"{'GAPS':>{col_w[4]}}"
    )
    print(header)
    print(thin)
    for r in docs:
        fname = r["filename"]
        if len(fname) > col_w[0]:
            fname = "..." + fname[-(col_w[0] - 3):]
        status = r["status"]
        score_str = f"{r['score']:.3f}" if r["score"] is not None else "n/a"
        cls = r.get("classification") or "n/a"
        gaps = len(r.get("gaps", []))
        print(
            f"{fname:<{col_w[0]}}  "
            f"{status:<{col_w[1]}}  "
            f"{score_str:<{col_w[2]}}  "
            f"{cls:<{col_w[3]}}  "
            f"{gaps:>{col_w[4]}}"
        )
    print(thin)
    print()

    # Aggregate stats
    print("  AGGREGATE STATISTICS")
    print(thin)
    print(f"  Total PDFs processed   : {stats['total_pdfs']}")
    print(f"  Conversion OK          : {stats['conversion_ok']}")
    print(f"  Conversion errors      : {stats['conversion_errors']}")
    print(f"  Validation errors      : {stats['validation_errors']}")
    print(f"  Pass rate              : {stats['pass_rate'] * 100:.1f}%")
    print(f"  Green rate             : {stats['green_rate'] * 100:.1f}%")
    if stats["avg_accessibility_score"] is not None:
        print(f"  Avg accessibility score: {stats['avg_accessibility_score']:.3f}")
    print(f"  Avg time per PDF       : {stats['avg_elapsed_s']:.2f}s")
    print(f"  Total elapsed          : {stats['total_elapsed_s']:.2f}s")
    print(f"  Validator available    : {stats['validator_available']}")
    print()

    # Classification breakdown
    print("  CLASSIFICATION BREAKDOWN")
    print(thin)
    total = stats["total_pdfs"] or 1
    for cls_label in ("GREEN", "YELLOW", "RED", "UNKNOWN"):
        n = counts.get(cls_label, 0)
        bar_len = int(n / total * 30)
        bar_str = "#" * bar_len
        print(f"  {cls_label:<8}  {n:>3}  {bar_str}")
    print()

    # Most common gap types
    top_gaps = gap_summary["top_gap_types"]
    if top_gaps:
        print("  MOST COMMON GAP TYPES")
        print(thin)
        for entry in top_gaps:
            print(f"  WCAG {entry['criterion']:<8}  {entry['count']:>3} occurrences")
        print()

    # RED items needing human attention
    red_items = gap_summary["red_items_requiring_attention"]
    if red_items:
        print(f"  RED ITEMS REQUIRING HUMAN ATTENTION  ({len(red_items)} total)")
        print(thin)
        for item in red_items[:20]:  # cap at 20 lines in terminal
            fname = item.get("file", "")
            crit = item.get("criterion", "")
            sev = item.get("severity", "")
            desc = item.get("description", "")
            if len(desc) > 60:
                desc = desc[:57] + "..."
            print(f"  [{sev:<8}]  WCAG {crit:<6}  {fname}: {desc}")
        if len(red_items) > 20:
            print(f"  ... and {len(red_items) - 20} more (see JSON report)")
        print()
    else:
        print("  No RED items found.")
        print()

    print(bar)
    print()


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def save_report(results: dict, output_path: Path) -> None:
    """Save detailed JSON report to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    logger.info("Report saved to %s", output_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m tests.auto_harness.runner \"C:\\\\path\\\\to\\\\corpus\"",
            file=sys.stderr,
        )
        sys.exit(1)

    corpus_path = Path(sys.argv[1])
    if not corpus_path.exists():
        print(f"Error: corpus directory does not exist: {corpus_path}", file=sys.stderr)
        sys.exit(1)
    if not corpus_path.is_dir():
        print(f"Error: path is not a directory: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    results = run_corpus(corpus_path)
    print_summary(results)

    # Build timestamped report filename
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = RESULTS_DIR / f"{ts}_gap_report.json"
    save_report(results, report_path)
    print(f"JSON report: {report_path}")

    # Exit with non-zero code if any RED documents exist
    red_count = results["gap_summary"]["classification_counts"].get("RED", 0)
    if red_count > 0:
        sys.exit(2)
