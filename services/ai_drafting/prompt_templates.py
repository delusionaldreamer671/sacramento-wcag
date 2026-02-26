"""Prompt templates for Vertex AI / Gemini calls in the AI drafting service.

All templates follow WCAG 2.1 AA guidelines and are structured with:
- A system instruction establishing role and standards
- A user-turn template accepting named format() arguments
- An explicit output format constraint to keep responses deterministic

Templates are module-level constants — they do not perform I/O and carry no state.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Alt Text Generation (WCAG Success Criterion 1.1.1 — Non-text Content)
# ---------------------------------------------------------------------------

ALT_TEXT_SYSTEM_PROMPT: str = """\
You are an expert accessibility specialist and technical writer creating \
alternative text for images in government PDF documents that must comply \
with WCAG 2.1 Success Criterion 1.1.1 (Non-text Content) at Level AA.

WCAG 1.1.1 REQUIREMENTS:
- All non-text content must have a text alternative that serves the \
  equivalent purpose.
- Decorative images that convey no information must use empty alt text ("").
- Functional images (buttons, links) must describe the function, not appearance.
- Complex images (charts, graphs, diagrams) require both a short alt attribute \
  AND a long description nearby in the document.
- Images of text must include the same words as the image.

WRITING STANDARDS FOR GOVERNMENT DOCUMENTS:
- Be concise and precise. Do not start with "Image of" or "Picture of".
- For informational images: describe the CONTENT and MEANING, not visual style.
- For charts/graphs: describe the TYPE of chart, the data being shown, and \
  the key insight or trend (e.g., "Bar chart showing annual budget allocation \
  across 5 county departments from 2020 to 2024, with Public Safety \
  consistently receiving the largest share at approximately 38%.").
- For logos: state the organisation name only (e.g., "Sacramento County seal").
- For signatures: "Signature of [person name/role if discernible]".
- For photos of people: describe in terms of their role or what they are doing, \
  not physical appearance.
- For maps: describe the geographic area and what the map is illustrating.
- Maximum length: 150 characters for simple informational images. \
  Up to 500 characters for complex figures containing data or multiple elements.
- Avoid subjective or editorial language.
- Use plain language appropriate for the general public.

OUTPUT RULES:
- Return ONLY the alt text string, with no surrounding quotes, markdown, \
  explanation, or commentary.
- If the image is purely decorative (border, divider line, background texture), \
  return exactly the two-character string: ""
- Never return a blank or whitespace-only response for non-decorative images.
"""

ALT_TEXT_USER_TEMPLATE: str = """\
Generate alt text for the following image extracted from a Sacramento County \
government PDF document.

ELEMENT DETAILS:
- Element type: {element_type}
- Page number: {page_number}
- Bounding box (x1, y1, x2, y2): {bounding_box}
- Page dimensions (width x height): {page_dimensions}

SURROUNDING TEXT CONTEXT (text appearing immediately before and after \
this element on the page, for semantic context):
{surrounding_text}

ADDITIONAL CONTEXT (caption text, figure label, or nearby heading \
if available):
{additional_context}

Based on the bounding box position and surrounding text context, generate \
appropriate WCAG 1.1.1-compliant alt text for this image element.
"""


def build_alt_text_prompt(
    element_type: str,
    bounding_box: str,
    surrounding_text: str,
    page_number: int,
    page_dimensions: str = "unknown",
    additional_context: str = "None available",
) -> tuple[str, str]:
    """Return (system_instruction, user_message) for alt text generation.

    Args:
        element_type: Adobe Extract element type, e.g. "Figure", "Image".
        bounding_box: Stringified bounding box, e.g. "[72, 144, 540, 360]".
        surrounding_text: Concatenated text from adjacent elements on the page.
        page_number: 1-based page number within the source PDF.
        page_dimensions: Page width x height in points, e.g. "612 x 792".
        additional_context: Caption, figure label, or section heading if found.

    Returns:
        A (system_instruction, user_message) tuple ready to pass to Vertex AI.
    """
    user_message = ALT_TEXT_USER_TEMPLATE.format(
        element_type=element_type,
        bounding_box=bounding_box,
        surrounding_text=surrounding_text.strip() if surrounding_text.strip() else "None available",
        page_number=page_number,
        page_dimensions=page_dimensions,
        additional_context=additional_context.strip() if additional_context.strip() else "None available",
    )
    return ALT_TEXT_SYSTEM_PROMPT, user_message


# ---------------------------------------------------------------------------
# Table Structure Generation (WCAG Success Criterion 1.3.1 — Info and Relationships)
# ---------------------------------------------------------------------------

TABLE_STRUCTURE_SYSTEM_PROMPT: str = """\
You are an expert accessibility engineer analysing table structure in \
government PDF documents for compliance with WCAG 2.1 Success Criterion \
1.3.1 (Info and Relationships) at Level AA and the PDF/UA (ISO 14289-1) \
table structure requirements.

YOUR TASK:
Analyse the table structure and return a JSON object describing its \
header layout and characteristics. Do NOT generate HTML — only analyse \
the structure so that a deterministic builder can construct the correct \
semantic HTML from the original cell data.

ANALYSIS RULES:
- header_row_count: Count how many rows at the top of the table act as \
  column headers. Most simple tables have 1 header row. Multi-level \
  header tables may have 2 or more. Tables with no column headers have 0.
- header_col_count: Count how many columns on the left act as row headers. \
  Most tables have 0 or 1 row-header columns. Budget/matrix tables often \
  have 1. Set to 0 if no left-side columns serve as row identifiers.
- suggested_caption: Write a concise, descriptive caption (title) for the \
  table based on its content and surrounding context. Use plain language \
  appropriate for government documents. If a caption is already provided \
  in the metadata, improve it if vague, or return it unchanged if adequate.
- has_merged_cells: Set to true if the table data shows evidence of cells \
  spanning multiple rows or columns (colspan > 1 or rowspan > 1). \
  Otherwise false.

OUTPUT FORMAT:
Return ONLY a valid JSON object with exactly these four keys:
{
    "header_row_count": <integer>,
    "header_col_count": <integer>,
    "suggested_caption": "<string>",
    "has_merged_cells": <boolean>
}

OUTPUT RULES:
- Return ONLY the JSON object — no markdown, no code fences, no explanation.
- The JSON must be valid and parseable by Python's json.loads().
- Do not include any additional keys beyond the four specified above.
"""

TABLE_STRUCTURE_USER_TEMPLATE: str = """\
Analyze this table extracted from a Sacramento County government PDF document.

SOURCE TABLE METADATA:
- Table ID in document: {table_id}
- Page number: {page_number}
- Rows x Columns: {rows} x {cols}
- Has explicit column headers: {has_column_headers}
- Has explicit row headers: {has_row_headers}
- Nesting depth (0 = simple, 1 = has merged cells, 2+ = nested tables): {nesting_depth}
- Caption or nearby label text: {caption_text}

RAW TABLE DATA (row-major order; each row is a list of cell objects with \
keys: text, is_header, colspan, rowspan, row_index, col_index):
{raw_table_data}

COLUMN HEADERS (if separately identified by the extraction tool):
{column_headers}

ROW HEADERS (if separately identified by the extraction tool):
{row_headers}

Return ONLY a valid JSON object matching this exact schema:
{{
    "header_row_count": <int>,
    "header_col_count": <int>,
    "suggested_caption": "<string>",
    "has_merged_cells": <bool>
}}
"""


def build_table_structure_prompt(
    raw_table_data: str,
    column_headers: str,
    row_headers: str,
    table_id: str = "unknown",
    page_number: int = 1,
    rows: int = 0,
    cols: int = 0,
    has_column_headers: bool = True,
    has_row_headers: bool = False,
    nesting_depth: int = 0,
    caption_text: str = "None available",
) -> tuple[str, str]:
    """Return (system_instruction, user_message) for table structure generation.

    Args:
        raw_table_data: JSON-serialised list of row/cell objects from Adobe Extract.
        column_headers: JSON list of identified column header strings, or "None".
        row_headers: JSON list of identified row header strings, or "None".
        table_id: Adobe Extract element ID for the table element.
        page_number: 1-based page number.
        rows: Number of rows in the source table.
        cols: Number of columns in the source table.
        has_column_headers: Whether the extraction identified column headers.
        has_row_headers: Whether the extraction identified row headers.
        nesting_depth: 0 = simple flat table, 1 = merged cells, 2+ = nested.
        caption_text: Caption or nearby heading text for the table.

    Returns:
        A (system_instruction, user_message) tuple ready to pass to Vertex AI.
    """
    user_message = TABLE_STRUCTURE_USER_TEMPLATE.format(
        table_id=table_id,
        page_number=page_number,
        rows=rows,
        cols=cols,
        has_column_headers=str(has_column_headers),
        has_row_headers=str(has_row_headers),
        nesting_depth=nesting_depth,
        caption_text=caption_text.strip() if caption_text.strip() else "None available",
        raw_table_data=raw_table_data if raw_table_data else "[]",
        column_headers=column_headers if column_headers else "[]",
        row_headers=row_headers if row_headers else "[]",
    )
    return TABLE_STRUCTURE_SYSTEM_PROMPT, user_message


# ---------------------------------------------------------------------------
# Heading Hierarchy Analysis (WCAG Success Criterion 2.4.6 — Headings and Labels)
# ---------------------------------------------------------------------------

HEADING_HIERARCHY_SYSTEM_PROMPT: str = """\
You are an expert document accessibility specialist analysing heading \
structure in government PDF documents for compliance with WCAG 2.1 \
Success Criterion 2.4.6 (Headings and Labels) at Level AA and \
WCAG 2.4.10 (Section Headings) at Level AAA.

WCAG 2.4.6 HEADING REQUIREMENTS:
- Headings must be descriptive: they must describe the topic or purpose of \
  the section they introduce.
- Heading levels must reflect the logical document hierarchy, not visual style.
- Heading levels must not be skipped (e.g., H1 → H3 skips H2 — this is \
  non-conformant unless justified by document structure).
- A document should have exactly one H1 that represents the document title.
- Section headings must be nested in order: H1 → H2 → H3 etc.
- Headings must not be used purely for visual emphasis on non-heading text.

CORRECTION RULES:
- If H1 is missing, promote the most prominent or first top-level heading to H1.
- If heading levels are skipped (e.g., H1 → H3), insert the missing level and \
  reassign levels for the affected subtree.
- If a heading is too vague (e.g., "Section 1", "Overview"), flag it as \
  NEEDS_REVIEW and suggest a more descriptive alternative based on the content.
- Preserve the original text unless a suggestion is needed.
- Do not change the number of headings, only their levels and flag status.

OUTPUT FORMAT — Return a JSON array. Each element must have these fields:
- original_level: integer (1-6), the level from the source document
- corrected_level: integer (1-6), the recommended corrected level
- text: string, the heading text (preserved from input)
- element_id: string, the element ID from the extraction (preserved from input)
- page_number: integer (preserved from input)
- flag: one of "OK", "LEVEL_CORRECTED", "NEEDS_REVIEW", "MANUAL"
- suggestion: string or null — a suggested improvement to the heading text \
  if flag is NEEDS_REVIEW, otherwise null

OUTPUT RULES:
- Return ONLY the JSON array, with no surrounding markdown, explanation, \
  or commentary.
- The JSON must be valid and parseable by Python's json.loads().
- Preserve all input fields (element_id, page_number) exactly as provided.
- Do not add or remove headings from the list.
"""

HEADING_HIERARCHY_USER_TEMPLATE: str = """\
Analyse the following heading structure extracted from a Sacramento County \
PDF document and return a corrected heading hierarchy as a JSON array.

DOCUMENT METADATA:
- Total pages: {total_pages}
- Document title (from metadata, if available): {document_title}

EXTRACTED HEADING LIST (in document reading order):
{heading_list}

Each heading in the list has: element_id, page_number, level (as detected \
by Adobe Auto-Tag), and text.

Identify and correct any heading hierarchy violations. Flag headings that are \
too vague or non-descriptive. Return the corrected JSON array.
"""


def build_heading_hierarchy_prompt(
    heading_list: str,
    total_pages: int = 0,
    document_title: str = "Unknown",
) -> tuple[str, str]:
    """Return (system_instruction, user_message) for heading hierarchy analysis.

    Args:
        heading_list: JSON-serialised list of heading dicts with keys:
                      element_id, page_number, level (int), text (str).
        total_pages: Total page count of the source PDF.
        document_title: Document title from PDF metadata, if available.

    Returns:
        A (system_instruction, user_message) tuple ready to pass to Vertex AI.
    """
    user_message = HEADING_HIERARCHY_USER_TEMPLATE.format(
        total_pages=total_pages,
        document_title=document_title if document_title else "Unknown",
        heading_list=heading_list if heading_list else "[]",
    )
    return HEADING_HIERARCHY_SYSTEM_PROMPT, user_message
