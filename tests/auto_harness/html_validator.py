"""
html_validator.py — Structural quality checks for WCAG-remediated HTML output.

Uses Python stdlib html.parser only (no third-party dependencies).
Each check returns a list of gap dictionaries. An empty list means the check passed.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


# ---------------------------------------------------------------------------
# Minimal DOM-like parser
# ---------------------------------------------------------------------------

class _Element:
    """Lightweight representation of an HTML element node."""

    __slots__ = ("tag", "attrs", "text", "children", "parent")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str | None],
        parent: "_Element | None" = None,
    ) -> None:
        self.tag: str = tag
        self.attrs: dict[str, str | None] = attrs
        self.text: str = ""          # direct character data (not from children)
        self.children: list[_Element] = []
        self.parent: "_Element | None" = parent

    def get_text(self) -> str:
        """Return concatenated text content of this element and all descendants."""
        parts = [self.text]
        for child in self.children:
            parts.append(child.get_text())
        return "".join(parts)

    def find_all(self, tag: str) -> list["_Element"]:
        """Depth-first search for all descendants with the given tag name."""
        results: list[_Element] = []
        for child in self.children:
            if child.tag == tag:
                results.append(child)
            results.extend(child.find_all(tag))
        return results

    def find(self, tag: str) -> "_Element | None":
        """Return first descendant with the given tag name, or None."""
        for child in self.children:
            if child.tag == tag:
                return child
            found = child.find(tag)
            if found is not None:
                return found
        return None

    def has_descendant(self, tag: str) -> bool:
        return self.find(tag) is not None


# Tags whose content is never meaningful for text extraction
_VOID_TAGS = frozenset(
    ["area", "base", "br", "col", "embed", "hr", "img", "input",
     "link", "meta", "param", "source", "track", "wbr"]
)

# Tags that are block-level and should not accumulate inline text
_BLOCK_TAGS = frozenset(
    ["html", "head", "body", "main", "header", "footer", "nav", "article",
     "section", "aside", "div", "table", "thead", "tbody", "tfoot", "tr",
     "ul", "ol", "dl", "figure", "fieldset", "form", "details", "summary"]
)


class _StructureParser(HTMLParser):
    """
    Builds a lightweight element tree from an HTML string.

    Public attributes after calling feed():
      root        — synthetic root element containing all parsed nodes
      html_el     — the <html> element if found, else None
      all_tags    — flat list of every element in document order
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("__root__", {})
        self._stack: list[_Element] = [self.root]
        self.html_el: _Element | None = None
        self.all_tags: list[_Element] = []

    # ------------------------------------------------------------------
    # HTMLParser callbacks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: v for k, v in attrs}
        parent = self._stack[-1]
        el = _Element(tag, attr_dict, parent)
        parent.children.append(el)
        self.all_tags.append(el)

        if tag == "html":
            self.html_el = el

        if tag not in _VOID_TAGS:
            self._stack.append(el)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        # Pop matching tag from stack (handle malformed HTML gracefully)
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                self._stack = self._stack[:i]
                return

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1].text += data

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags (e.g., <br/>, <img/>)
        self.handle_starttag(tag, attrs)
        # Do NOT push onto stack — void semantics

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def find_all(self, tag: str) -> list[_Element]:
        return self.root.find_all(tag)

    def find(self, tag: str) -> _Element | None:
        return self.root.find(tag)

    def full_text(self) -> str:
        return self.root.get_text()


def _parse(html: str) -> _StructureParser:
    parser = _StructureParser()
    parser.feed(html)
    return parser


# ---------------------------------------------------------------------------
# Gap construction helper
# ---------------------------------------------------------------------------

def _gap(
    check_id: str,
    severity: str,
    classification: str,
    description: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "severity": severity,
        "classification": classification,
        "description": description,
        "details": details or {},
    }


# ---------------------------------------------------------------------------
# Check 1 — LANG_ATTRIBUTE
# ---------------------------------------------------------------------------

def check_lang_attribute(html: str) -> list[dict[str, Any]]:
    """
    GREEN / minor — <html lang="..."> must be present and non-empty.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []

    if p.html_el is None:
        gaps.append(_gap(
            "LANG_ATTRIBUTE",
            "minor",
            "GREEN",
            "<html> element not found in document.",
            {"html_element_found": False},
        ))
        return gaps

    lang = p.html_el.attrs.get("lang", None)
    if not lang or not lang.strip():
        gaps.append(_gap(
            "LANG_ATTRIBUTE",
            "minor",
            "GREEN",
            "<html> element is missing a non-empty lang attribute (required for WCAG 3.1.1).",
            {"lang_value": lang},
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 2 — TABLE_EXISTS
# ---------------------------------------------------------------------------

# Patterns that suggest the source document contains tabular/fee data
_TABLE_HINT_PATTERNS = re.compile(
    r"(\$\d|\bfee\b|\bschedule\b|\brate\b|\btariff\b|\bassessment\b|\bcharge\b)",
    re.IGNORECASE,
)


def check_table_exists(html: str) -> list[dict[str, Any]]:
    """
    RED / critical — If source text indicates tabular content (dollar signs, fee/schedule
    keywords) but the output has NO <table> elements, flag it.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []

    full_text = p.full_text()
    hints = _TABLE_HINT_PATTERNS.findall(full_text)
    tables = p.find_all("table")

    if hints and not tables:
        gaps.append(_gap(
            "TABLE_EXISTS",
            "critical",
            "RED",
            (
                "Document text contains table-like patterns "
                f"({len(hints)} hint(s): {sorted(set(h.lower() for h in hints))}) "
                "but no <table> elements were found — content may have been flattened to <p> tags."
            ),
            {
                "table_count": 0,
                "hint_count": len(hints),
                "hint_samples": list(dict.fromkeys(h.lower() for h in hints))[:5],
            },
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 3 — TABLE_HEADERS
# ---------------------------------------------------------------------------

def check_table_headers(html: str) -> list[dict[str, Any]]:
    """
    GREEN / serious — Every <table> must have a <thead> containing at least one
    <th scope="col"> element.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    tables = p.find_all("table")

    for idx, table in enumerate(tables):
        thead = table.find("thead")
        if thead is None:
            gaps.append(_gap(
                "TABLE_HEADERS",
                "serious",
                "GREEN",
                f"Table {idx + 1} is missing a <thead> element (required for WCAG 1.3.1).",
                {"table_index": idx + 1, "issue": "missing_thead"},
            ))
            continue

        th_elements = thead.find_all("th")
        scope_col_ths = [
            th for th in th_elements
            if (th.attrs.get("scope") or "").lower() == "col"
        ]

        if not th_elements:
            gaps.append(_gap(
                "TABLE_HEADERS",
                "serious",
                "GREEN",
                f"Table {idx + 1} has <thead> but no <th> elements inside it.",
                {"table_index": idx + 1, "issue": "empty_thead"},
            ))
        elif not scope_col_ths:
            gaps.append(_gap(
                "TABLE_HEADERS",
                "serious",
                "GREEN",
                (
                    f"Table {idx + 1} has <th> elements but none have "
                    'scope="col" (required for column association per WCAG 1.3.1).'
                ),
                {
                    "table_index": idx + 1,
                    "issue": "missing_scope_col",
                    "th_count": len(th_elements),
                },
            ))

    return gaps


# ---------------------------------------------------------------------------
# Check 4 — TABLE_STRUCTURE
# ---------------------------------------------------------------------------

def check_table_structure(html: str) -> list[dict[str, Any]]:
    """
    GREEN / moderate — Each table must have <tbody>; body rows must have a consistent
    column count that matches the header column count.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    tables = p.find_all("table")

    for idx, table in enumerate(tables):
        # Check for <tbody>
        tbody = table.find("tbody")
        if tbody is None:
            # Tables with only headers (form templates) are valid — skip
            thead = table.find("thead")
            if thead is not None and thead.find_all("th"):
                continue  # header-only table is OK (blank form template)
            gaps.append(_gap(
                "TABLE_STRUCTURE",
                "moderate",
                "GREEN",
                f"Table {idx + 1} is missing a <tbody> element.",
                {"table_index": idx + 1, "issue": "missing_tbody"},
            ))
            continue

        # Determine header column count (accounting for colspan)
        thead = table.find("thead")
        header_col_count: int | None = None
        if thead is not None:
            header_rows = thead.find_all("tr")
            if header_rows:
                total_h = 0
                for cell in header_rows[0].children:
                    if cell.tag in ("th", "td"):
                        cs = cell.attrs.get("colspan", "1")
                        try:
                            total_h += max(1, int(cs))
                        except (ValueError, TypeError):
                            total_h += 1
                header_col_count = total_h if total_h > 0 else None

        # Check body row column counts (accounting for colspan)
        body_rows = tbody.find_all("tr")
        if not body_rows:
            gaps.append(_gap(
                "TABLE_STRUCTURE",
                "moderate",
                "GREEN",
                f"Table {idx + 1} has an empty <tbody> (no <tr> rows).",
                {"table_index": idx + 1, "issue": "empty_tbody"},
            ))
            continue

        def _effective_cols(row: _Element) -> int:
            """Count effective columns including colspan."""
            total = 0
            for cell in row.children:
                if cell.tag in ("td", "th"):
                    colspan = cell.attrs.get("colspan", "1")
                    try:
                        total += max(1, int(colspan))
                    except (ValueError, TypeError):
                        total += 1
            return total

        col_counts = [_effective_cols(row) for row in body_rows]
        unique_counts = set(col_counts)

        if len(unique_counts) > 1:
            gaps.append(_gap(
                "TABLE_STRUCTURE",
                "moderate",
                "GREEN",
                (
                    f"Table {idx + 1} body rows have inconsistent column counts: "
                    f"{sorted(unique_counts)}. All rows should have the same number of cells."
                ),
                {
                    "table_index": idx + 1,
                    "issue": "inconsistent_col_counts",
                    "col_counts": col_counts,
                },
            ))
        elif header_col_count is not None and col_counts and col_counts[0] != header_col_count:
            gaps.append(_gap(
                "TABLE_STRUCTURE",
                "moderate",
                "GREEN",
                (
                    f"Table {idx + 1} body rows have {col_counts[0]} columns "
                    f"but the header row has {header_col_count} columns."
                ),
                {
                    "table_index": idx + 1,
                    "issue": "header_body_col_mismatch",
                    "header_cols": header_col_count,
                    "body_cols": col_counts[0],
                },
            ))

    return gaps


# ---------------------------------------------------------------------------
# Check 5 — FOOTNOTE_LEAK
# ---------------------------------------------------------------------------

_FOOTNOTE_PATTERNS = re.compile(
    r"(\[[\w\d]+\]|pursuant|equation|parcel|per unit|see note|footnote|endnote|ibid)",
    re.IGNORECASE,
)


def check_footnote_leak(html: str) -> list[dict[str, Any]]:
    """
    GREEN / moderate — No <td> rows after the last normal data row that contain
    footnote-like text (starting with "[", or containing "pursuant", "equation",
    "parcel", etc.).  These indicate extraction artefacts that leaked into table body.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    tables = p.find_all("table")

    for idx, table in enumerate(tables):
        tbody = table.find("tbody")
        if tbody is None:
            continue

        rows = tbody.find_all("tr")
        leaking_rows: list[dict[str, Any]] = []

        for row_idx, row in enumerate(rows):
            cells = row.find_all("td")
            # A footnote row typically has 1 wide cell (possibly with colspan)
            if len(cells) != 1:
                continue
            cell = cells[0]
            # Check if this cell has colspan (spanning full table width = footnote)
            has_colspan = cell.attrs.get("colspan") is not None
            cell_text = cell.get_text().strip()
            if not cell_text:
                continue

            is_footnote = (
                cell_text.startswith("[")
                or bool(_FOOTNOTE_PATTERNS.search(cell_text))
            )
            if is_footnote:
                leaking_rows.append({
                    "row_index": row_idx + 1,
                    "text_preview": cell_text[:120],
                })

        if leaking_rows:
            gaps.append(_gap(
                "FOOTNOTE_LEAK",
                "moderate",
                "GREEN",
                (
                    f"Table {idx + 1} contains {len(leaking_rows)} row(s) that appear to be "
                    "footnotes or explanatory text leaked into the table body."
                ),
                {
                    "table_index": idx + 1,
                    "leaking_row_count": len(leaking_rows),
                    "leaking_rows": leaking_rows,
                },
            ))

    return gaps


# ---------------------------------------------------------------------------
# Check 6 — HEADING_HIERARCHY
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def check_heading_hierarchy(html: str) -> list[dict[str, Any]]:
    """
    YELLOW / moderate — Heading levels must not skip (e.g., h1 directly to h3 with
    no h2 in between).
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []

    # Collect headings in document order
    headings = [el for el in p.all_tags if el.tag in _HEADING_TAGS]

    if not headings:
        return gaps  # No headings — nothing to validate here

    skip_pairs: list[dict[str, Any]] = []
    prev_level = int(headings[0].tag[1])

    for el in headings[1:]:
        level = int(el.tag[1])
        if level > prev_level + 1:
            skip_pairs.append({
                "from": f"h{prev_level}",
                "to": f"h{level}",
                "heading_text": el.get_text().strip()[:80],
            })
        prev_level = level

    if skip_pairs:
        gaps.append(_gap(
            "HEADING_HIERARCHY",
            "moderate",
            "YELLOW",
            (
                f"Heading levels skip {len(skip_pairs)} time(s). "
                "Skipping heading levels breaks document outline structure (WCAG 2.4.6)."
            ),
            {"skipped_levels": skip_pairs},
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 7 — NO_EMPTY_ELEMENTS
# ---------------------------------------------------------------------------

_EMPTY_CHECK_TAGS = frozenset(["p", "td", "th"])


def check_no_empty_elements(html: str) -> list[dict[str, Any]]:
    """
    GREEN / minor — No <p>, <td>, or <th> elements should be entirely empty
    (no text content and no meaningful child elements).
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    empty_elements: list[dict[str, Any]] = []

    for el in p.all_tags:
        if el.tag not in _EMPTY_CHECK_TAGS:
            continue
        text = el.get_text().strip()
        # Allow cells that contain void/inline elements like <img> (they provide content)
        has_img = bool(el.find_all("img"))
        has_input = bool(el.find_all("input"))
        if not text and not has_img and not has_input:
            empty_elements.append({
                "tag": el.tag,
                "attrs": dict(el.attrs),
            })

    if empty_elements:
        counts = {}
        for e in empty_elements:
            counts[e["tag"]] = counts.get(e["tag"], 0) + 1

        gaps.append(_gap(
            "NO_EMPTY_ELEMENTS",
            "minor",
            "GREEN",
            (
                f"Found {len(empty_elements)} empty element(s) "
                f"({', '.join(f'{v} <{k}>' for k, v in sorted(counts.items()))}). "
                "Empty semantic elements add noise to the accessibility tree."
            ),
            {
                "empty_count": len(empty_elements),
                "by_tag": counts,
                "elements": empty_elements[:20],  # cap detail list
            },
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 8 — SEMANTIC_STRUCTURE
# ---------------------------------------------------------------------------

def check_semantic_structure(html: str) -> list[dict[str, Any]]:
    """
    YELLOW / moderate — Document should have a <main> landmark and at least one
    heading or table to provide navigable structure.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    issues: list[str] = []

    main_el = p.find("main")
    if main_el is None:
        issues.append("missing <main> landmark element")

    has_heading = any(el.tag in _HEADING_TAGS for el in p.all_tags)
    has_table = bool(p.find_all("table"))

    if not has_heading and not has_table:
        issues.append("no headings or tables found — document has no navigable structure")

    if issues:
        gaps.append(_gap(
            "SEMANTIC_STRUCTURE",
            "moderate",
            "YELLOW",
            "Document is missing structural landmarks: " + "; ".join(issues) + ".",
            {
                "has_main": main_el is not None,
                "has_heading": has_heading,
                "has_table": has_table,
                "issues": issues,
            },
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 9 — IMAGE_ALT_TEXT
# ---------------------------------------------------------------------------

def check_image_alt_text(html: str) -> list[dict[str, Any]]:
    """
    GREEN / serious — Every <img> must have a non-empty alt attribute
    (decorative images should use alt="", but purely missing alt is a violation).
    WCAG 1.1.1.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    images = p.find_all("img")

    missing_alt: list[dict[str, Any]] = []
    for img in images:
        if "alt" not in img.attrs:
            missing_alt.append({
                "src": img.attrs.get("src", "(no src)"),
                "issue": "missing_alt_attribute",
            })

    if missing_alt:
        gaps.append(_gap(
            "IMAGE_ALT_TEXT",
            "serious",
            "GREEN",
            (
                f"{len(missing_alt)} image(s) are missing the alt attribute entirely "
                "(WCAG 1.1.1 — Non-text Content)."
            ),
            {
                "image_count": len(images),
                "missing_alt_count": len(missing_alt),
                "missing_alt_images": missing_alt[:20],
            },
        ))

    return gaps


# ---------------------------------------------------------------------------
# Check 10 — EQUATION_FILTER
# ---------------------------------------------------------------------------

_EQUATION_PATTERN = re.compile(r"Equation\s*\[", re.IGNORECASE)


def check_equation_filter(html: str) -> list[dict[str, Any]]:
    """
    GREEN / minor — Text content should not contain "Equation [" or "Equation["
    as standalone content — these are extraction artefacts from mathematical
    notation that was not properly converted.
    """
    p = _parse(html)
    gaps: list[dict[str, Any]] = []
    leaking_elements: list[dict[str, Any]] = []

    for el in p.all_tags:
        # Only check leaf-like elements (p, td, th, li, span, div without block children)
        if el.tag not in {"p", "td", "th", "li", "span", "div", "caption"}:
            continue
        text = el.get_text().strip()
        if _EQUATION_PATTERN.search(text):
            leaking_elements.append({
                "tag": el.tag,
                "text_preview": text[:120],
            })

    if leaking_elements:
        gaps.append(_gap(
            "EQUATION_FILTER",
            "minor",
            "GREEN",
            (
                f"Found {len(leaking_elements)} element(s) containing 'Equation [' — "
                "these are likely extraction artefacts from mathematical notation "
                "that was not properly converted to accessible markup."
            ),
            {
                "count": len(leaking_elements),
                "elements": leaking_elements[:10],
            },
        ))

    return gaps


# ---------------------------------------------------------------------------
# All registered checks (ordered)
# ---------------------------------------------------------------------------

_CHECKS = [
    check_lang_attribute,
    check_table_exists,
    check_table_headers,
    check_table_structure,
    check_footnote_leak,
    check_heading_hierarchy,
    check_no_empty_elements,
    check_semantic_structure,
    check_image_alt_text,
    check_equation_filter,
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_html(html: str, filename: str = "") -> dict[str, Any]:
    """
    Run all structural quality checks on a WCAG-remediated HTML string.

    Parameters
    ----------
    html : str
        Full HTML document string to validate.
    filename : str, optional
        Source filename for reporting purposes.

    Returns
    -------
    dict with keys:
        filename    : str
        total_checks: int    — number of checks executed
        passed      : int    — checks that produced no gaps
        failed      : int    — checks that produced at least one gap
        gaps        : list   — all gap dicts from all failed checks
        score       : float  — passed / total_checks * 100 (percentage)
    """
    all_gaps: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for check_fn in _CHECKS:
        result = check_fn(html)
        if result:
            all_gaps.extend(result)
            failed += 1
        else:
            passed += 1

    total = passed + failed
    score = round((passed / total) * 100, 2) if total > 0 else 0.0

    return {
        "filename": filename,
        "total_checks": total,
        "passed": passed,
        "failed": failed,
        "gaps": all_gaps,
        "score": score,
    }
