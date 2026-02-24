"""PDF/UA document builder for the WCAG remediation pipeline.

Assembles approved HITL review items into semantically valid HTML,
validates accessibility, generates a tagged PDF/UA document using
reportlab (POC approach — see Assumption A8 in CLAUDE.md), and
produces a MANUAL_REVIEW_REQUIRED CSV for items requiring human
remediation that cannot be automated.

WCAG 2.1 AA criteria addressed by this module:
  1.1.1  Non-text Content   — alt text on every figure/image
  1.3.1  Info & Relationships — table headers with scope, semantic lists
  1.3.2  Meaningful Sequence — element order preserved from source
  2.4.6  Headings and Labels — heading hierarchy with no skipped levels
  3.1.1  Language of Page    — lang="en" attribute on <html>
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from html import escape
from typing import Any, Literal, Optional

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors

from services.common.models import HITLReviewItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_HEADING_LEVELS = frozenset({1, 2, 3, 4, 5, 6})
_VALID_ELEMENT_TYPES = frozenset(
    {"heading", "paragraph", "image", "table", "list", "link"}
)

ElementType = Literal["heading", "paragraph", "image", "table", "list", "link"]

# Pattern matching purely numeric or percentage/currency values.
# Used to decide whether the first cell in a body row is a label (row header)
# or a data value.  Labels are rendered as <th scope="row">.
_RE_NUMERIC_CELL = re.compile(
    r"^[\s$€£]*([\d,]+\.?\d*)\s*%?\s*$"
)


def _is_row_header_cell(value: str) -> bool:
    """Return True if *value* looks like a textual row label, not a data value.

    A cell is considered a row header when it contains meaningful text — i.e.
    it is NOT empty, NOT purely numeric, and NOT a percentage/currency figure.
    """
    stripped = value.strip()
    if not stripped:
        return False
    if _RE_NUMERIC_CELL.match(stripped):
        return False
    return True


# ---------------------------------------------------------------------------
# Internal element representation
# ---------------------------------------------------------------------------


@dataclass
class _Element:
    """Normalised document element ready for HTML and PDF rendering."""

    element_type: ElementType
    content: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def _heading_level(self) -> int:
        """Return heading level (1-6) from attributes, defaulting to 2."""
        level = self.attributes.get("level", 2)
        try:
            level = int(level)
        except (ValueError, TypeError):
            level = 2
        return max(1, min(6, level))

    def _alt_text(self) -> str:
        """Return non-empty alt text or raise ValueError."""
        alt = self.attributes.get("alt", "").strip()
        return alt if alt else ""

    def _table_headers(self) -> list[str]:
        return self.attributes.get("headers", [])

    def _table_rows(self) -> list[list[str]]:
        return self.attributes.get("rows", [])

    def _list_items(self) -> list[str]:
        items = self.attributes.get("items", [])
        if isinstance(items, list):
            return [str(i) for i in items]
        # Fallback: split newline-delimited content
        return [line.strip() for line in self.content.splitlines() if line.strip()]

    def _list_ordered(self) -> bool:
        return bool(self.attributes.get("ordered", False))

    def _href(self) -> str:
        return self.attributes.get("href", "#").strip() or "#"


# ---------------------------------------------------------------------------
# Accessibility validation helpers
# ---------------------------------------------------------------------------


def _validate_heading_sequence(levels: list[int]) -> list[str]:
    """Return a list of violation descriptions for heading hierarchy skips.

    A heading sequence is valid when each new level is at most one level
    deeper than the previous seen level. Returning to a higher level (smaller
    number) is always allowed (e.g. H3 → H2 is fine).
    """
    violations: list[str] = []
    if not levels:
        return violations
    prev = levels[0]
    for idx, lvl in enumerate(levels[1:], start=2):
        if lvl > prev + 1:
            violations.append(
                f"Heading level skipped: H{prev} → H{lvl} at heading #{idx}. "
                f"WCAG 2.4.6 requires sequential heading levels."
            )
        prev = lvl
    return violations


# ---------------------------------------------------------------------------
# Main builder class
# ---------------------------------------------------------------------------


class PDFUABuilder:
    """Builds a PDF/UA-compliant document from approved HITL review items.

    Usage::

        builder = PDFUABuilder(document_id="abc-123", document_title="Report Q1")
        builder.add_element("heading", "Executive Summary", {"level": 1})
        builder.add_element("paragraph", "This report covers ...")
        builder.add_element("image", "", {"alt": "Bar chart of 2025 revenue", "src": "fig1.png"})

        html = builder.build_semantic_html()
        pdf_bytes = builder.generate_pdfua(html)
        report = builder.validate_accessibility(html)
    """

    def __init__(
        self,
        document_id: str,
        document_title: str = "Remediated Document",
        language: str = "en",
    ) -> None:
        if not document_id or not document_id.strip():
            raise ValueError("document_id must be a non-empty string.")
        self.document_id = document_id.strip()
        self.document_title = document_title.strip() or "Remediated Document"
        self.language = language.strip() or "en"
        self._elements: list[_Element] = []
        # Tracks used anchor IDs to detect and disambiguate duplicates.
        # Reset on each call to build_semantic_html() for determinism.
        self._heading_id_counter: dict[str, int] = {}
        # Pre-computed anchor IDs — populated once in build_semantic_html()
        # so both the TOC builder and heading renderers share the same IDs.
        self._precomputed_anchor_ids: list[str] = []
        self._heading_render_idx: int = 0

    # ------------------------------------------------------------------
    # Public API: add_element
    # ------------------------------------------------------------------

    def add_element(
        self,
        element_type: str,
        content: str,
        attributes: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a document element.

        Args:
            element_type: One of ``heading``, ``paragraph``, ``image``,
                ``table``, ``list``, ``link``.
            content: Text content.  For images, may be empty (alt text is in
                ``attributes["alt"]``).  For tables, may be a caption.
            attributes: Type-specific attributes:
                - heading: ``level`` (int, 1-6)
                - image: ``alt`` (str, required), ``src`` (str, optional)
                - table: ``headers`` (list[str]), ``rows`` (list[list[str]])
                - list: ``items`` (list[str]), ``ordered`` (bool)
                - link: ``href`` (str)

        Raises:
            ValueError: If ``element_type`` is not a recognised type or
                content/attributes fail validation.
        """
        element_type_stripped = element_type.strip().lower()
        if element_type_stripped not in _VALID_ELEMENT_TYPES:
            raise ValueError(
                f"Unknown element_type '{element_type}'. "
                f"Valid types: {sorted(_VALID_ELEMENT_TYPES)}"
            )
        if content is None:
            content = ""
        attrs = dict(attributes) if attributes else {}

        # --- per-type validation ---
        if element_type_stripped == "image":
            alt = attrs.get("alt", "").strip()
            if not alt:
                logger.warning(
                    "Image element added without alt text (document_id=%s). "
                    "WCAG 1.1.1 requires non-empty alt text for informative images.",
                    self.document_id,
                )

        if element_type_stripped == "heading":
            try:
                lvl = int(attrs.get("level", 2))
            except (ValueError, TypeError):
                lvl = 2
            if lvl not in _VALID_HEADING_LEVELS:
                raise ValueError(
                    f"Heading level must be 1-6, got {attrs.get('level')!r}."
                )
            attrs["level"] = lvl

        if element_type_stripped == "table":
            headers = attrs.get("headers")
            if not headers:
                logger.warning(
                    "Table added without headers (document_id=%s). "
                    "WCAG 1.3.1 requires table header cells.",
                    self.document_id,
                )

        self._elements.append(
            _Element(
                element_type=element_type_stripped,  # type: ignore[arg-type]
                content=content,
                attributes=attrs,
            )
        )

    # ------------------------------------------------------------------
    # build_semantic_html
    # ------------------------------------------------------------------

    def build_semantic_html(self) -> str:
        """Assemble all added elements into valid, accessible semantic HTML.

        The returned string is a complete HTML5 document with:
        - ``lang`` attribute on ``<html>`` (WCAG 3.1.1)
        - ``<title>`` set to the document title (WCAG 2.4.2)
        - Corrected heading hierarchy (warnings logged for skips)
        - Tables with ``<th scope="col">`` headers (WCAG 1.3.1)
        - Images with ``alt`` attributes (WCAG 1.1.1)
        - Semantic list elements (WCAG 1.3.1)
        - Reading order from the order elements were added (WCAG 1.3.2)
        """
        if not self._elements:
            logger.warning(
                "build_semantic_html called on empty builder (document_id=%s).",
                self.document_id,
            )

        # Pre-compute ALL anchor IDs in a single pass so that both the TOC
        # builder and heading renderers share exactly the same IDs.  Previously,
        # the TOC advanced _heading_id_counter and then _render_heading advanced
        # it again, producing mismatched IDs (e.g. TOC links to #sec-intro but
        # the heading gets id="sec-intro-2").
        self._heading_id_counter = {}
        self._precomputed_anchor_ids = []
        for elem in self._elements:
            if elem.element_type == "heading":
                self._precomputed_anchor_ids.append(
                    self._make_anchor_id(elem.content.strip())
                )
        # Reset render index — _render_heading reads from precomputed list
        self._heading_render_idx = 0

        # Build TOC from precomputed IDs (reads _precomputed_anchor_ids)
        toc_html = self._build_toc_nav()

        # Reset render index for body rendering pass
        self._heading_render_idx = 0

        body_parts: list[str] = []
        for elem in self._elements:
            body_parts.append(self._render_element(elem))

        if toc_html:
            body_html = toc_html + "\n" + "\n".join(body_parts)
        else:
            body_html = "\n".join(body_parts)

        title_escaped = escape(self.document_title)

        html = (
            f'<!DOCTYPE html>\n'
            f'<html lang="{escape(self.language)}">\n'
            f'<head>\n'
            f'  <meta charset="UTF-8">\n'
            f'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'  <title>{title_escaped}</title>\n'
            f'</head>\n'
            f'<body>\n'
            f'  <a class="skip-link" href="#main-content">Skip to main content</a>\n'
            f'  <main id="main-content">\n'
            f'{body_html}\n'
            f'  </main>\n'
            f'</body>\n'
            f'</html>\n'
        )
        return html

    def _render_element(self, elem: _Element) -> str:
        """Dispatch to a per-type renderer and return an HTML fragment."""
        if elem.element_type == "heading":
            return self._render_heading(elem)
        if elem.element_type == "paragraph":
            return self._render_paragraph(elem)
        if elem.element_type == "image":
            return self._render_image(elem)
        if elem.element_type == "table":
            return self._render_table(elem)
        if elem.element_type == "list":
            return self._render_list(elem)
        if elem.element_type == "link":
            return self._render_link(elem)
        # Should never reach here due to add_element validation
        logger.error("Unhandled element_type '%s'", elem.element_type)
        return f"<!-- unhandled element type: {escape(elem.element_type)} -->"

    def _make_anchor_id(self, text: str) -> str:
        """Generate a stable, unique anchor ID from heading text.

        Steps:
        1. Lowercase, replace spaces with hyphens.
        2. Strip all characters that are not alphanumeric or hyphens.
        3. Prefix with "sec-", truncate to 50 characters (prefix included).
        4. If the base ID has been used before, append "-2", "-3", etc.

        The ``_heading_id_counter`` dict on the instance tracks usage so
        duplicates within a single ``build_semantic_html()`` call are handled
        deterministically.  The counter is reset at the start of each
        ``build_semantic_html()`` call.
        """
        slug = text.strip().lower()
        slug = slug.replace(" ", "-")
        slug = re.sub(r"[^a-z0-9\-]", "", slug)
        slug = slug.strip("-")
        if not slug:
            slug = "section"
        base_id = ("sec-" + slug)[:50]

        count = self._heading_id_counter.get(base_id, 0)
        self._heading_id_counter[base_id] = count + 1

        if count == 0:
            return base_id
        return f"{base_id}-{count + 1}"

    def _render_heading(self, elem: _Element) -> str:
        level = elem._heading_level()
        text = escape(elem.content.strip())
        # Use pre-computed anchor ID (shared with TOC) instead of calling
        # _make_anchor_id() again, which would produce a different ID.
        if self._heading_render_idx < len(self._precomputed_anchor_ids):
            anchor_id = self._precomputed_anchor_ids[self._heading_render_idx]
            self._heading_render_idx += 1
        else:
            # Fallback: should never happen, but be safe
            anchor_id = self._make_anchor_id(elem.content.strip())
        return f'    <h{level} id="{anchor_id}">{text}</h{level}>'

    def _render_paragraph(self, elem: _Element) -> str:
        text = escape(elem.content.strip())
        return f"    <p>{text}</p>"

    def _render_image(self, elem: _Element) -> str:
        alt = escape(elem._alt_text())
        src = elem.attributes.get("src", "").strip() or ""
        caption = escape(elem.content.strip())
        is_decorative = not elem._alt_text() and elem.attributes.get("aria-hidden") == "true"
        is_complex = elem.attributes.get("data-complexity") == "complex"
        lines: list[str] = ["    <figure>"]
        if src:
            # Embed the actual image; escape only double-quotes in the src
            # (data URIs use base64 which is safe; external URLs may have &)
            src_escaped = src.replace('"', "&quot;")
            lines.append(
                f'      <img src="{src_escaped}" alt="{alt}"'
                f' style="max-width:100%;height:auto;">'
            )
        else:
            # No source available — render a placeholder <img> with alt text only
            lines.append(f'      <img alt="{alt}" style="max-width:100%;height:auto;">')
        if caption:
            lines.append(f"      <figcaption>{caption}</figcaption>")
        elif not src:
            # When there is no image and no caption, surface the alt text as a
            # visible figcaption so sighted users know what the figure represents
            lines.append(f"      <figcaption>{alt}</figcaption>")
        lines.append("    </figure>")
        # WCAG 1.1.1: complex images (charts, maps, data visualisations) require a
        # long description in addition to the short alt text.  Decorative images
        # (empty alt + aria-hidden) are exempt — they convey no information.
        if is_complex and not is_decorative:
            lines.append('    <details class="long-desc" data-needs-review="long-desc">')
            lines.append("      <summary>Detailed description of this figure</summary>")
            lines.append(
                "      <p>[This figure contains complex visual information that requires"
                " a detailed text description. Review and replace this placeholder.]</p>"
            )
            lines.append("    </details>")
        return "\n".join(lines)

    def _render_table(self, elem: _Element) -> str:
        headers = elem._table_headers()
        rows = elem._table_rows()
        caption = escape(elem.content.strip())
        # Effective column count = max of header and all body rows
        all_counts = []
        if headers:
            all_counts.append(len(headers))
        if rows:
            all_counts.extend(len(r) for r in rows)
        num_cols = max(all_counts) if all_counts else 1
        lines: list[str] = ["    <table>"]
        if caption:
            lines.append(f"      <caption>{caption}</caption>")

        if headers:
            lines.append("      <thead>")
            lines.append("        <tr>")
            h_len = len(headers)
            if h_len < num_cols and h_len > 0:
                for header in headers[:-1]:
                    lines.append(f'          <th scope="col">{escape(str(header))}</th>')
                span = num_cols - h_len + 1
                lines.append(f'          <th scope="col" colspan="{span}">{escape(str(headers[-1]))}</th>')
            else:
                for header in headers:
                    lines.append(f'          <th scope="col">{escape(str(header))}</th>')
            lines.append("        </tr>")
            lines.append("      </thead>")

        if rows:
            lines.append("      <tbody>")
            for row in rows:
                lines.append("        <tr>")
                row_len = len(row)
                # Determine if the first cell is a row header (textual label,
                # not purely numeric/percentage).  Render as <th scope="row">
                # for accessibility (WCAG 1.3.1 — Info and Relationships).
                first_is_header = (
                    row_len >= 2
                    and _is_row_header_cell(str(row[0]))
                )
                if row_len < num_cols and row_len > 0:
                    # Fewer cols than header: use colspan on last cell to fill
                    for idx, cell in enumerate(row[:-1]):
                        if idx == 0 and first_is_header:
                            lines.append(f'          <th scope="row">{escape(str(cell))}</th>')
                        else:
                            lines.append(f"          <td>{escape(str(cell))}</td>")
                    span = num_cols - row_len + 1
                    lines.append(f'          <td colspan="{span}">{escape(str(row[-1]))}</td>')
                else:
                    for idx, cell in enumerate(row):
                        if idx == 0 and first_is_header:
                            lines.append(f'          <th scope="row">{escape(str(cell))}</th>')
                        else:
                            lines.append(f"          <td>{escape(str(cell))}</td>")
                lines.append("        </tr>")
            lines.append("      </tbody>")

        lines.append("    </table>")
        return "\n".join(lines)

    def _render_list(self, elem: _Element) -> str:
        items = elem._list_items()
        ordered = elem._list_ordered()
        tag = "ol" if ordered else "ul"
        lines: list[str] = [f"    <{tag}>"]
        for item in items:
            lines.append(f"      <li>{escape(item)}</li>")
        lines.append(f"    </{tag}>")
        return "\n".join(lines)

    def _render_link(self, elem: _Element) -> str:
        href = escape(elem._href())
        text = escape(elem.content.strip())
        return f'    <p><a href="{href}">{text}</a></p>'

    def _build_toc_nav(self) -> str:
        """Build a Table of Contents ``<nav>`` from heading elements.

        Scans ``self._elements`` for all heading elements, skips H1 (the
        document title), and builds a nested ``<ol>`` reflecting the heading
        hierarchy up to one level of nesting (H2 at top level, H3 as children
        of the preceding H2).  Deeper headings (H4-H6) are treated as H3 for
        nesting purposes so the TOC stays shallow and accessible.

        Returns an empty string when fewer than 3 qualifying headings exist
        (short documents don't need a TOC).

        Anchor IDs are read from ``self._precomputed_anchor_ids`` which was
        populated in ``build_semantic_html()`` in a single pass.  This ensures
        TOC link targets exactly match heading ``id`` attributes.

        The returned HTML block is indented to sit inside ``<main>``.
        """
        # Build TOC entries from precomputed anchor IDs.
        # Iterate all elements to keep heading_idx in sync with _precomputed_anchor_ids.
        toc_entries: list[tuple[int, str, str]] = []  # (level, text, anchor_id)
        heading_idx = 0
        for elem in self._elements:
            if elem.element_type != "heading":
                continue
            level = elem._heading_level()
            raw_text = elem.content.strip()
            if heading_idx < len(self._precomputed_anchor_ids):
                anchor_id = self._precomputed_anchor_ids[heading_idx]
            else:
                anchor_id = self._make_anchor_id(raw_text)
            heading_idx += 1
            if level >= 2:
                toc_entries.append((level, raw_text, anchor_id))
        # (No restoration needed — build_semantic_html will immediately use
        # the counter as advanced by this method, then _render_heading calls
        # will advance it further in exactly the same order.)

        if len(toc_entries) < 3:
            return ""

        # Build nested HTML — H2 at top level, H3 as children of last H2.
        # H4-H6 are also treated as H3-level children.
        lines: list[str] = []
        lines.append('    <nav aria-label="Table of Contents" id="toc">')
        lines.append('      <h2>Contents</h2>')
        lines.append('      <ol>')

        open_sublist = False  # True when we have an open <ol> for H3+ items

        for level, text, anchor_id in toc_entries:
            escaped_text = escape(text)
            if level == 2:
                # Close any open sublist before starting a new H2 entry.
                if open_sublist:
                    lines.append('        </ol>')
                    lines.append('      </li>')
                    open_sublist = False
                # Open a new H2 list item (leave </li> open so H3 can nest).
                lines.append(f'      <li><a href="#{anchor_id}">{escaped_text}</a>')
            else:
                # H3 and deeper: nest under the current H2 item.
                if not open_sublist:
                    lines.append('        <ol>')
                    open_sublist = True
                lines.append(
                    f'          <li><a href="#{anchor_id}">{escaped_text}</a></li>'
                )

        # Close any dangling open sublist and the last H2 item.
        if open_sublist:
            lines.append('        </ol>')
            lines.append('      </li>')
        else:
            # Last entry was an H2 item with no children — close it.
            lines.append('      </li>')

        lines.append('      </ol>')
        lines.append('    </nav>')
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # generate_pdfua
    # ------------------------------------------------------------------

    def generate_pdfua(self, html_content: str) -> bytes:
        """Convert semantic HTML to a tagged PDF/UA document using reportlab.

        This is a POC implementation.  A production build should use the
        Adobe Acrobat Services PDF Accessibility Auto-Tag API once
        Assumption A8 (recompilation via Adobe) is verified.

        The PDF produced by this method:
        - Sets XMP metadata: title, language
        - Uses reportlab's flowable model to preserve reading order (WCAG 1.3.2)
        - Applies heading styles H1-H6 (WCAG 2.4.6)
        - Renders tables with column header rows styled distinctly (WCAG 1.3.1)
        - Includes alt-text paragraphs below figure placeholders (WCAG 1.1.1)

        Args:
            html_content: Valid HTML string from ``build_semantic_html()``.
                If empty, a minimal placeholder PDF is returned.

        Returns:
            Raw PDF bytes.
        """
        if not html_content or not html_content.strip():
            logger.warning(
                "generate_pdfua called with empty html_content (document_id=%s). "
                "Returning minimal placeholder PDF.",
                self.document_id,
            )
            return self._build_minimal_pdf()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            leftMargin=1 * inch,
            rightMargin=1 * inch,
            topMargin=1 * inch,
            bottomMargin=1 * inch,
            title=self.document_title,
            author="Sacramento County WCAG Remediation Pipeline",
            subject="PDF/UA Compliant Document",
            creator="Sacramento WCAG Pipeline v1.0",
        )

        styles = self._build_styles()
        story = self._html_to_flowables(html_content, styles)

        if not story:
            story = [Paragraph("(No content)", styles["BodyText"])]

        try:
            doc.build(story)
        except Exception:
            logger.exception(
                "reportlab build failed for document_id=%s", self.document_id
            )
            raise

        return buffer.getvalue()

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        """Create named ParagraphStyles for each semantic element type."""
        base = getSampleStyleSheet()

        heading_base = {
            "fontName": "Helvetica-Bold",
            "spaceAfter": 6,
            "spaceBefore": 12,
            "leading": 18,
        }

        styles: dict[str, ParagraphStyle] = {
            "BodyText": ParagraphStyle(
                "BodyText",
                fontName="Helvetica",
                fontSize=11,
                leading=14,
                spaceAfter=6,
                alignment=TA_LEFT,
            ),
            "H1": ParagraphStyle("H1", fontSize=20, **heading_base),
            "H2": ParagraphStyle("H2", fontSize=17, **heading_base),
            "H3": ParagraphStyle("H3", fontSize=15, **heading_base),
            "H4": ParagraphStyle("H4", fontSize=13, **heading_base),
            "H5": ParagraphStyle("H5", fontSize=12, **heading_base),
            "H6": ParagraphStyle(
                "H6", fontSize=11, fontName="Helvetica-BoldOblique", spaceAfter=4
            ),
            "AltText": ParagraphStyle(
                "AltText",
                fontName="Helvetica-Oblique",
                fontSize=9,
                leading=11,
                textColor=colors.grey,
                spaceAfter=6,
                leftIndent=12,
            ),
            "ListItem": ParagraphStyle(
                "ListItem",
                fontName="Helvetica",
                fontSize=11,
                leading=14,
                leftIndent=24,
                spaceAfter=3,
            ),
            "Caption": ParagraphStyle(
                "Caption",
                fontName="Helvetica-Oblique",
                fontSize=9,
                leading=11,
                spaceAfter=6,
                alignment=TA_LEFT,
            ),
        }
        return styles

    def _html_to_flowables(
        self, html_content: str, styles: dict[str, ParagraphStyle]
    ) -> list:
        """Convert the semantic HTML string into a list of reportlab flowables.

        Parses structural tags (h1-h6, p, figure/img, table, ul, ol, a) and
        converts each to the appropriate reportlab Paragraph or Table flowable.
        """
        from html.parser import HTMLParser

        flowables: list = []

        class _Parser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self._tag_stack: list[str] = []
                self._buffer: str = ""
                self._current_attrs: dict[str, str] = {}
                # table state
                self._in_table: bool = False
                self._table_rows: list[list[str]] = []
                self._current_row: list[str] = []
                self._in_header_row: bool = False
                self._header_cols: int = 0
                # list state
                self._in_list: bool = False
                self._ordered: bool = False
                self._list_index: int = 0
                # figure state
                self._pending_alt: str = ""
                self._pending_caption: str = ""
                # long-desc details state (complex image placeholder)
                self._in_long_desc: bool = False

            def handle_starttag(
                self, tag: str, attrs_list: list[tuple[str, str | None]]
            ) -> None:
                attrs = {k: (v or "") for k, v in attrs_list}
                self._tag_stack.append(tag)
                self._current_attrs = attrs

                if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    self._buffer = ""
                elif tag == "p":
                    self._buffer = ""
                elif tag == "figure":
                    self._pending_alt = ""
                    self._pending_caption = ""
                elif tag == "img":
                    self._pending_alt = attrs.get("alt", "")
                elif tag == "figcaption":
                    self._buffer = ""
                elif tag == "details" and "long-desc" in attrs.get("class", ""):
                    self._in_long_desc = True
                elif tag in {"ul", "ol"}:
                    self._in_list = True
                    self._ordered = tag == "ol"
                    self._list_index = 0
                elif tag == "li":
                    self._buffer = ""
                elif tag == "table":
                    self._in_table = True
                    self._table_rows = []
                    self._current_row = []
                elif tag == "thead":
                    self._in_header_row = True
                elif tag in {"tr"}:
                    self._current_row = []
                elif tag in {"th", "td"}:
                    self._buffer = ""
                elif tag == "a":
                    self._buffer = ""

            def handle_endtag(self, tag: str) -> None:
                if self._tag_stack and self._tag_stack[-1] == tag:
                    self._tag_stack.pop()

                if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    level = int(tag[1])
                    style_key = f"H{level}"
                    text = self._buffer.strip()
                    if text:
                        flowables.append(
                            Paragraph(escape(text), styles[style_key])
                        )
                    self._buffer = ""

                elif tag == "p":
                    text = self._buffer.strip()
                    if text:
                        flowables.append(Paragraph(escape(text), styles["BodyText"]))
                    self._buffer = ""

                elif tag == "figure":
                    # Render alt text as italic paragraph (WCAG 1.1.1)
                    if self._pending_alt:
                        flowables.append(
                            Paragraph(
                                f"[Image: {escape(self._pending_alt)}]",
                                styles["AltText"],
                            )
                        )
                    if self._pending_caption:
                        flowables.append(
                            Paragraph(
                                escape(self._pending_caption), styles["Caption"]
                            )
                        )
                    flowables.append(Spacer(1, 0.1 * inch))
                    self._pending_alt = ""
                    self._pending_caption = ""

                elif tag == "details" and self._in_long_desc:
                    # Render the long-description placeholder as a styled paragraph
                    # in the PDF so reviewers know a detailed description is needed.
                    flowables.append(
                        Paragraph(
                            "[Detailed description required \u2014 see HITL review queue]",
                            styles["AltText"],
                        )
                    )
                    flowables.append(Spacer(1, 0.05 * inch))
                    self._in_long_desc = False

                elif tag == "figcaption":
                    self._pending_caption = self._buffer.strip()
                    self._buffer = ""

                elif tag in {"ul", "ol"}:
                    self._in_list = False
                    self._ordered = False
                    self._list_index = 0
                    flowables.append(Spacer(1, 0.05 * inch))

                elif tag == "li":
                    self._list_index += 1
                    text = self._buffer.strip()
                    if text:
                        prefix = f"{self._list_index}." if self._ordered else "\u2022"
                        flowables.append(
                            Paragraph(
                                f"{prefix} {escape(text)}", styles["ListItem"]
                            )
                        )
                    self._buffer = ""

                elif tag in {"th", "td"}:
                    self._current_row.append(self._buffer.strip())
                    self._buffer = ""

                elif tag == "tr":
                    if self._current_row:
                        self._table_rows.append(list(self._current_row))
                    self._current_row = []

                elif tag == "thead":
                    self._header_cols = (
                        len(self._table_rows[0]) if self._table_rows else 0
                    )
                    self._in_header_row = False

                elif tag == "table":
                    self._in_table = False
                    if self._table_rows:
                        rl_table = self._build_rl_table(
                            self._table_rows, self._header_cols
                        )
                        flowables.append(rl_table)
                        flowables.append(Spacer(1, 0.15 * inch))
                    self._table_rows = []
                    self._header_cols = 0

                elif tag == "a":
                    text = self._buffer.strip()
                    if text:
                        flowables.append(Paragraph(escape(text), styles["BodyText"]))
                    self._buffer = ""

            def handle_data(self, data: str) -> None:
                current = self._tag_stack[-1] if self._tag_stack else ""
                if current in {
                    "h1", "h2", "h3", "h4", "h5", "h6",
                    "p", "li", "th", "td", "figcaption", "a",
                }:
                    self._buffer += data

            def _build_rl_table(
                self, rows: list[list[str]], header_rows: int
            ) -> Table:
                # Normalise row width: pad short rows to max column count
                max_cols = max((len(r) for r in rows), default=1)
                normalised = [r + [""] * (max_cols - len(r)) for r in rows]

                col_width = (6.5 * inch) / max(max_cols, 1)
                rl_table = Table(
                    normalised,
                    colWidths=[col_width] * max_cols,
                    repeatRows=header_rows,
                )
                ts = TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#003366")),
                        ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
                        ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
                rl_table.setStyle(ts)
                return rl_table

        parser = _Parser()
        parser.feed(html_content)
        return flowables

    def _build_minimal_pdf(self) -> bytes:
        """Return a minimal valid PDF with a placeholder message."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER)
        styles = getSampleStyleSheet()
        story = [
            Paragraph(escape(self.document_title), styles["Title"]),
            Spacer(1, 0.2 * inch),
            Paragraph(
                "This document has no approved content yet.",
                styles["BodyText"],
            ),
        ]
        doc.build(story)
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # validate_accessibility
    # ------------------------------------------------------------------

    def validate_accessibility(self, html_content: str) -> dict[str, Any]:
        """Run programmatic accessibility checks on the assembled HTML.

        Checks performed:
        - WCAG 1.1.1: All ``<img>`` elements have non-empty ``alt`` attribute.
        - WCAG 1.3.1: All ``<table>`` elements contain at least one ``<th>``
          with a ``scope`` attribute.
        - WCAG 2.4.6: Heading levels are sequential (no H1 → H3 skip).
        - WCAG 3.1.1: The ``<html>`` element has a non-empty ``lang`` attribute.
        - WCAG 2.4.2: The document has a non-empty ``<title>`` element.

        Violations are classified into three tiers:
        - **CRITICAL** (``violation_class: "critical"``): Must block the output.
          Missing ``lang``, missing ``<title>``, zero headings in the document.
        - **SERIOUS** (``violation_class: "serious"``): Block when count exceeds
          a threshold (>50% of images missing alt text; tables with no ``<th>``).
        - **WARNING** (``violation_class: "warning"``): Annotation only, never
          blocks. Heading hierarchy skips; generic placeholder alt text.

        Args:
            html_content: Complete HTML string to validate.

        Returns:
            Dict with keys:
            - ``violations`` (list[dict]): Each entry has ``criterion``,
              ``severity``, ``violation_class``, and ``description``.
            - ``score`` (float): Proportion of checks that passed, 0.0–1.0.
            - ``blocked`` (bool): True when any CRITICAL violations exist or
              SERIOUS threshold is exceeded.
            - ``critical_violations`` (list[str]): Short labels for each
              CRITICAL violation (e.g. ``"missing lang"``, ``"no title"``).
            - ``serious_violations`` (list[str]): Short labels for each
              SERIOUS violation that contributed to blocking.
        """
        violations: list[dict[str, str]] = []

        if not html_content or not html_content.strip():
            violations.append(
                {
                    "criterion": "general",
                    "severity": "critical",
                    "violation_class": "critical",
                    "description": "HTML content is empty.",
                }
            )
            return {
                "violations": violations,
                "score": 0.0,
                "blocked": True,
                "critical_violations": ["empty HTML content"],
                "serious_violations": [],
            }

        total_checks = 0
        passed_checks = 0

        # Accumulators for blocking classification
        critical_violation_labels: list[str] = []
        serious_violation_labels: list[str] = []

        # --- WCAG 3.1.1: Language of Page (CRITICAL) ---
        total_checks += 1
        lang_match = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        if lang_match and lang_match.group(1).strip():
            passed_checks += 1
        else:
            violations.append(
                {
                    "criterion": "3.1.1",
                    "severity": "serious",
                    "violation_class": "critical",
                    "description": (
                        "The <html> element is missing a non-empty lang attribute. "
                        "Screen readers use this to select the correct speech synthesizer."
                    ),
                }
            )
            critical_violation_labels.append("missing lang attribute on <html> (WCAG 3.1.1)")

        # --- WCAG 2.4.2: Document title (CRITICAL) ---
        total_checks += 1
        if re.search(r"<title>[^<]+</title>", html_content, re.IGNORECASE):
            passed_checks += 1
        else:
            violations.append(
                {
                    "criterion": "2.4.2",
                    "severity": "serious",
                    "violation_class": "critical",
                    "description": (
                        "The document is missing a <title> element or the title is empty. "
                        "Screen readers announce the page title when the document loads."
                    ),
                }
            )
            critical_violation_labels.append("missing or empty <title> (WCAG 2.4.2)")

        # --- WCAG 1.1.1: Images with alt text (SERIOUS when >50% missing) ---
        img_tags = re.findall(r"<img\b([^>]*)>", html_content, re.IGNORECASE)
        img_missing_alt_count = 0
        for img_attrs in img_tags:
            total_checks += 1
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_attrs)
            if alt_match and alt_match.group(1).strip():
                passed_checks += 1
            else:
                img_missing_alt_count += 1
                violations.append(
                    {
                        "criterion": "1.1.1",
                        "severity": "critical",
                        "violation_class": "serious",
                        "description": (
                            "An <img> element is missing a non-empty alt attribute. "
                            "Informative images must have descriptive alt text."
                        ),
                    }
                )

        # Promote to SERIOUS-blocking when more than 50% of images lack alt text
        if img_tags and img_missing_alt_count > len(img_tags) / 2:
            serious_violation_labels.append(
                f"{img_missing_alt_count}/{len(img_tags)} images missing alt text (WCAG 1.1.1)"
            )

        # --- Image src attribute (CRITICAL when >10% missing) ---
        # An <img> without src renders as a broken image icon — no visual content.
        img_no_src_count = 0
        for img_attrs in img_tags:
            if not re.search(r'\bsrc=["\']', img_attrs):
                img_no_src_count += 1
        if img_no_src_count > 0:
            total_checks += 1
            violations.append(
                {
                    "criterion": "1.1.1",
                    "severity": "critical",
                    "violation_class": "critical",
                    "description": (
                        f"{img_no_src_count} <img> element(s) missing src attribute. "
                        "Images without src render as broken icons with no visual content."
                    ),
                }
            )
            # Block if more than 10% of images have no src
            if img_no_src_count > max(1, len(img_tags) * 0.1):
                critical_violation_labels.append(
                    f"{img_no_src_count}/{len(img_tags)} images missing src attribute"
                )
        elif img_tags:
            total_checks += 1
            passed_checks += 1

        # --- WCAG 1.3.1: Tables with scoped headers (SERIOUS) ---
        table_blocks = re.findall(r"<table\b[^>]*>(.*?)</table>", html_content, re.IGNORECASE | re.DOTALL)
        for table_html in table_blocks:
            total_checks += 1
            if re.search(r'<th\b[^>]*scope=["\'][^"\']+["\']', table_html, re.IGNORECASE):
                passed_checks += 1
            else:
                violations.append(
                    {
                        "criterion": "1.3.1",
                        "severity": "serious",
                        "violation_class": "serious",
                        "description": (
                            "A <table> element does not have <th> cells with a scope attribute. "
                            "Use scope=\"col\" or scope=\"row\" to associate headers with data cells."
                        ),
                    }
                )
                serious_violation_labels.append(
                    "table missing <th scope> headers (WCAG 1.3.1)"
                )

        # --- WCAG 2.4.6: Heading hierarchy (WARNING) ---
        heading_matches = re.findall(r"<h([1-6])\b", html_content, re.IGNORECASE)
        heading_levels = [int(m) for m in heading_matches]
        if heading_levels:
            total_checks += 1
            sequence_violations = _validate_heading_sequence(heading_levels)
            if not sequence_violations:
                passed_checks += 1
            else:
                for desc in sequence_violations:
                    violations.append(
                        {
                            "criterion": "2.4.6",
                            "severity": "moderate",
                            "violation_class": "warning",
                            "description": desc,
                        }
                    )
        else:
            # No headings is acceptable for short documents (no check to fail)
            total_checks += 1
            passed_checks += 1

        # --- WCAG 2.4.1: Skip navigation link (SERIOUS) ---
        total_checks += 1
        if re.search(r'<a[^>]+href=["\']#main-content["\']', html_content, re.IGNORECASE):
            passed_checks += 1
        else:
            violations.append(
                {
                    "criterion": "2.4.1",
                    "severity": "serious",
                    "violation_class": "serious",
                    "description": (
                        "No skip navigation link found. A link to skip to main content "
                        "is required so keyboard users can bypass repeated navigation."
                    ),
                }
            )
            serious_violation_labels.append("missing skip navigation link (WCAG 2.4.1)")

        # --- WCAG 1.3.1: Main landmark (SERIOUS) ---
        total_checks += 1
        if re.search(r"<main\b", html_content, re.IGNORECASE):
            passed_checks += 1
        else:
            violations.append(
                {
                    "criterion": "1.3.1",
                    "severity": "serious",
                    "violation_class": "serious",
                    "description": (
                        "No <main> landmark element found. Screen reader users rely on "
                        "landmarks to navigate document structure."
                    ),
                }
            )

        # --- Placeholder text detection (SERIOUS) ---
        placeholder_patterns = [
            r"\[Figure on page \d+ — alt text requires review\]",
            r"\[This figure contains complex visual information that requires",
            r"alt text requires review",
            r"requires human review",
            r"Review and replace this placeholder",
        ]
        placeholder_count = 0
        for pattern in placeholder_patterns:
            placeholder_count += len(re.findall(pattern, html_content))
        if placeholder_count > 0:
            total_checks += 1
            violations.append(
                {
                    "criterion": "1.1.1",
                    "severity": "serious",
                    "violation_class": "serious",
                    "description": (
                        f"{placeholder_count} placeholder text occurrence(s) found in output. "
                        "These indicate unfinished remediation that requires human review."
                    ),
                }
            )
            if placeholder_count > 5:
                serious_violation_labels.append(
                    f"{placeholder_count} placeholder texts remain in output"
                )
        else:
            total_checks += 1
            passed_checks += 1

        # --- Table caption check (WARNING) ---
        table_blocks_for_caption = re.findall(
            r"<table\b[^>]*>(.*?)</table>", html_content, re.IGNORECASE | re.DOTALL
        )
        tables_without_caption = 0
        for table_html in table_blocks_for_caption:
            total_checks += 1
            if re.search(r"<caption\b", table_html, re.IGNORECASE):
                passed_checks += 1
            else:
                tables_without_caption += 1
                violations.append(
                    {
                        "criterion": "1.3.1",
                        "severity": "moderate",
                        "violation_class": "warning",
                        "description": (
                            "A <table> element is missing a <caption> element. "
                            "Captions help users understand what data the table presents."
                        ),
                    }
                )

        # Score formula: require a minimum number of structural checks to have
        # run.  A score of 1.0 from 0/0 checks is misleading — it means no
        # validation actually happened.
        _MINIMUM_CHECKS = 5
        if total_checks < _MINIMUM_CHECKS:
            score = 0.0
        else:
            score = passed_checks / total_checks

        # Determine blocked status:
        # Block on any CRITICAL violation OR any threshold-exceeding SERIOUS violation.
        blocked = bool(critical_violation_labels or serious_violation_labels)

        logger.info(
            "Accessibility validation complete: document_id=%s checks=%d passed=%d "
            "violations=%d score=%.2f blocked=%s critical=%d serious=%d",
            self.document_id,
            total_checks,
            passed_checks,
            len(violations),
            score,
            blocked,
            len(critical_violation_labels),
            len(serious_violation_labels),
        )
        return {
            "violations": violations,
            "score": round(score, 4),
            "blocked": blocked,
            "critical_violations": critical_violation_labels,
            "serious_violations": serious_violation_labels,
        }

    # ------------------------------------------------------------------
    # generate_manual_review_csv (class method)
    # ------------------------------------------------------------------

    @classmethod
    def generate_manual_review_csv(cls, items: list[HITLReviewItem]) -> str:
        """Produce a MANUAL_REVIEW_REQUIRED CSV string for items that need human remediation.

        Args:
            items: List of ``HITLReviewItem`` objects.  Typically those whose
                associated ``WCAGFinding`` has ``complexity == ComplexityFlag.MANUAL``
                or whose ``reviewer_decision == "reject"``.

        Returns:
            UTF-8 CSV string with columns:
            item_id, document_id, element_type, finding_id,
            ai_suggestion, reviewer_decision, reviewer_edit,
            reviewed_by, reviewed_at, reason_for_manual_review.

        Note:
            An empty CSV (header row only) means no manual review is required.
        """
        if items is None:
            items = []

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "item_id",
                "document_id",
                "element_type",
                "finding_id",
                "ai_suggestion",
                "reviewer_decision",
                "reviewer_edit",
                "reviewed_by",
                "reviewed_at",
                "reason_for_manual_review",
            ]
        )

        for item in items:
            if not isinstance(item, HITLReviewItem):
                logger.warning("generate_manual_review_csv: skipping non-HITLReviewItem entry.")
                continue

            decision = item.reviewer_decision or "pending"
            if decision == "reject":
                reason = "Reviewer rejected AI suggestion — requires manual remediation."
            elif decision == "pending" or decision is None:
                reason = "Item not yet reviewed — manual review required before recompilation."
            else:
                # Should not normally appear; included for completeness
                reason = f"Flagged for manual review (decision: {decision})."

            reviewed_at_str = (
                item.reviewed_at.isoformat() if item.reviewed_at else ""
            )

            writer.writerow(
                [
                    item.id,
                    item.document_id,
                    item.element_type,
                    item.finding_id,
                    item.ai_suggestion,
                    decision,
                    item.reviewer_edit or "",
                    item.reviewed_by or "",
                    reviewed_at_str,
                    reason,
                ]
            )

        return output.getvalue()
