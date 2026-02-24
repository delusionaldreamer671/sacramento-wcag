"""PDF chunking for large documents exceeding Adobe API page limits.

Splits oversized PDFs into processable chunks using pikepdf, processes
each chunk independently, and merges results with page-offset correction.

Adobe Extract API limits:
  - Non-scanned: 400 pages
  - Scanned: 150 pages
  - Heavy tables: may be lower

Conservative targets:
  - Non-scanned: 250 pages per chunk (headroom below 400)
  - Scanned: 100 pages per chunk (headroom below 150)
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pikepdf import — guarded for graceful degradation
# ---------------------------------------------------------------------------

try:
    import pikepdf  # noqa: F401
    _PIKEPDF_AVAILABLE = True
except ImportError:
    _PIKEPDF_AVAILABLE = False
    logger.warning(
        "pikepdf not installed. PDF chunking will be unavailable. "
        "Install with: pip install pikepdf"
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ChunkInfo:
    """Metadata for a single PDF chunk produced by chunk_pdf()."""

    chunk_index: int
    start_page: int    # 0-based, inclusive
    end_page: int      # 0-based, exclusive
    page_count: int
    pdf_path: Path
    overlap_start: int  # Number of overlap pages at start (0 for first chunk)
    overlap_end: int    # Number of overlap pages at end (0 for last chunk)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_page_count(pdf_path: Path) -> int:
    """Return PDF page count via pikepdf. Returns 0 on any error."""
    if not _PIKEPDF_AVAILABLE:
        return 0
    try:
        import pikepdf
        with pikepdf.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_page_count: failed to open %s: %s", pdf_path, exc)
        return 0


def needs_chunking(pdf_path: Path, max_pages: int = 250) -> bool:
    """Return True if the PDF exceeds max_pages and must be chunked.

    Returns False on any error (pikepdf unavailable, corrupt PDF, etc.)
    so callers can safely proceed with single-file processing.
    """
    if not _PIKEPDF_AVAILABLE:
        return False
    try:
        count = get_page_count(pdf_path)
        result = count > max_pages
        if result:
            logger.info(
                "needs_chunking: %s has %d pages (limit %d) — chunking required",
                pdf_path.name, count, max_pages,
            )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("needs_chunking: error checking %s: %s", pdf_path, exc)
        return False


def chunk_pdf(
    pdf_path: Path,
    max_pages: int = 250,
    overlap: int = 2,
) -> list[ChunkInfo]:
    """Split a PDF into chunks of at most max_pages pages each.

    Adjacent chunks share `overlap` pages at their boundary to handle elements
    spanning page boundaries. Chunk PDFs are written to a temp directory — the
    caller is responsible for cleanup (e.g. shutil.rmtree(chunk.pdf_path.parent)).

    A heuristic avoids splitting mid-table: boundary pages with very few content
    bytes (continuation pages) are shifted forward by up to 2 pages. Returns []
    if pikepdf is unavailable or the PDF cannot be opened.
    """
    if not _PIKEPDF_AVAILABLE:
        logger.warning("chunk_pdf: pikepdf not available — cannot chunk %s", pdf_path)
        return []

    try:
        import pikepdf
    except ImportError:
        return []

    tmp_dir = Path(tempfile.mkdtemp(prefix="wcag_chunks_"))
    logger.info(
        "chunk_pdf: splitting %s into %d-page chunks (overlap=%d) → %s",
        pdf_path.name, max_pages, overlap, tmp_dir,
    )

    try:
        with pikepdf.open(str(pdf_path)) as src_pdf:
            total_pages = len(src_pdf.pages)
            if total_pages == 0:
                logger.warning("chunk_pdf: %s has 0 pages", pdf_path)
                return []

            boundaries = _compute_boundaries(src_pdf, total_pages, max_pages, overlap)
            chunks: list[ChunkInfo] = []

            for chunk_idx, (start, end, ov_start, ov_end) in enumerate(boundaries):
                chunk_path = tmp_dir / f"chunk_{chunk_idx:03d}_p{start + 1}-{end}.pdf"
                with pikepdf.new() as chunk_pdf:
                    for page_num in range(start, end):
                        chunk_pdf.pages.append(src_pdf.pages[page_num])
                    chunk_pdf.save(str(chunk_path))

                chunks.append(ChunkInfo(
                    chunk_index=chunk_idx,
                    start_page=start,
                    end_page=end,
                    page_count=end - start,
                    pdf_path=chunk_path,
                    overlap_start=ov_start,
                    overlap_end=ov_end,
                ))
                logger.info(
                    "chunk_pdf: chunk %d — pages %d-%d (%d pages) → %s",
                    chunk_idx, start + 1, end, end - start, chunk_path.name,
                )

            logger.info(
                "chunk_pdf: produced %d chunks from %d-page %s",
                len(chunks), total_pages, pdf_path.name,
            )
            return chunks

    except Exception as exc:  # noqa: BLE001
        logger.error("chunk_pdf: failed to chunk %s: %s", pdf_path, exc)
        return []


def merge_extraction_results(
    chunk_results: list[tuple[ChunkInfo, dict[str, Any]]],
    total_pages: int,
) -> dict[str, Any]:
    """Merge Adobe Extract JSON dicts from multiple chunks into a single result.

    For each element, the ``"Page"`` field is corrected by adding the chunk's
    ``start_page`` offset so page numbers reflect the original document.

    Overlap elements (pages shared between adjacent chunks) are de-duplicated
    using a hash of (adjusted_page, text, path). Only the first occurrence
    (from the earlier chunk) is kept.

    Returns ``{"elements": []}`` if chunk_results is empty.
    """
    if not chunk_results:
        logger.warning("merge_extraction_results: no chunk results to merge")
        return {"elements": []}

    merged_elements: list[dict[str, Any]] = []
    merged_figures: dict[str, str] = {}
    seen_hashes: set[str] = set()
    duplicate_count = 0

    for chunk_info, extract_json in chunk_results:
        page_offset = chunk_info.start_page
        chunk_idx = chunk_info.chunk_index

        # Merge figure images from this chunk, prefixed to avoid collisions
        chunk_figures: dict[str, str] = extract_json.get("_figure_images", {})
        figure_remap: dict[str, str] = {}
        for orig_path, data_uri in chunk_figures.items():
            # Prefix with chunk index to ensure uniqueness across chunks
            new_path = f"chunk{chunk_idx}/{orig_path}"
            merged_figures[new_path] = data_uri
            figure_remap[orig_path] = new_path

        for elem in extract_json.get("elements", []):
            adjusted_elem = {**elem, "Page": elem.get("Page", 0) + page_offset}

            # Remap filePaths to chunk-prefixed versions
            file_paths = adjusted_elem.get("filePaths")
            if file_paths and figure_remap:
                adjusted_elem["filePaths"] = [
                    figure_remap.get(fp, fp) for fp in file_paths
                ]

            key = _element_hash(adjusted_elem)
            if key in seen_hashes:
                duplicate_count += 1
                continue
            seen_hashes.add(key)
            merged_elements.append(adjusted_elem)

    logger.info(
        "merge_extraction_results: %d chunks → %d elements (%d duplicates removed, %d figures)",
        len(chunk_results), len(merged_elements), duplicate_count, len(merged_figures),
    )
    result: dict[str, Any] = {
        "elements": merged_elements,
        "extended_metadata": {
            "page_count": total_pages,
            "chunked": True,
            "chunk_count": len(chunk_results),
        },
    }
    if merged_figures:
        result["_figure_images"] = merged_figures
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_boundaries(
    src_pdf: Any,
    total_pages: int,
    max_pages: int,
    overlap: int,
) -> list[tuple[int, int, int, int]]:
    """Compute (start, end, overlap_start, overlap_end) tuples for each chunk.

    All page values are 0-based; end is exclusive. Adjacent chunks share
    `overlap` pages at their shared boundary.
    """
    safe_overlap = min(overlap, max(0, max_pages // 10))
    stride = max(1, max_pages - safe_overlap)
    starts = list(range(0, total_pages, stride))
    n = len(starts)
    boundaries: list[tuple[int, int, int, int]] = []

    for i, raw_start in enumerate(starts):
        is_first = i == 0
        is_last = i == n - 1
        raw_end = min(raw_start + max_pages, total_pages)
        adjusted_end = _adjust_boundary(src_pdf, raw_end, total_pages, shift=2)
        ov_start = 0 if is_first else safe_overlap
        ov_end = 0 if is_last else safe_overlap
        end_with_overlap = min(adjusted_end + (0 if is_last else safe_overlap), total_pages)
        boundaries.append((raw_start, end_with_overlap, ov_start, ov_end))

    return boundaries


def _adjust_boundary(
    src_pdf: Any,
    boundary_page: int,
    total_pages: int,
    shift: int = 2,
) -> int:
    """Shift the chunk boundary forward if the boundary page is a short continuation page.

    A page is considered a continuation when its /Contents stream is very
    small (<= 500 bytes), which typically indicates a table row without a
    header — an undesirable split point. The boundary shifts forward by at
    most `shift` pages to find a more natural break.
    """
    if boundary_page >= total_pages:
        return boundary_page
    try:
        for offset in range(shift):
            check_page = boundary_page + offset
            if check_page >= total_pages:
                break
            page = src_pdf.pages[check_page]
            contents = page.get("/Contents")
            if contents is None:
                continue  # Empty page — keep looking
            try:
                if hasattr(contents, "read_bytes"):
                    content_bytes = len(contents.read_bytes())
                elif hasattr(contents, "__iter__"):
                    content_bytes = sum(
                        len(s.read_bytes()) for s in contents if hasattr(s, "read_bytes")
                    )
                else:
                    content_bytes = 1000
            except Exception:  # noqa: BLE001
                content_bytes = 1000
            if content_bytes > 500:
                return check_page  # Non-trivial page — good split point
        return boundary_page
    except Exception as exc:  # noqa: BLE001
        logger.debug("_adjust_boundary: inspection failed at page %d: %s", boundary_page, exc)
        return boundary_page


def _element_hash(elem: dict[str, Any]) -> str:
    """Stable SHA-256 deduplication hash for an Adobe Extract element.

    Hashes (adjusted_page, text, path). Elements with identical text on the
    same adjusted page are treated as duplicates regardless of bounding box.
    """
    key = "\x00".join([
        str(elem.get("Page", 0)),
        (elem.get("Text") or "").strip(),
        elem.get("Path", ""),
    ]).encode("utf-8")
    return hashlib.sha256(key).hexdigest()
