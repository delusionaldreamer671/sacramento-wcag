"""Tests for services/ai_drafting/prompt_templates.py.

All functions under test are pure — they accept string/int arguments and return
(system_instruction, user_message) tuples. No Vertex AI calls are made.
Tests verify prompt content, structure, and WCAG criterion references.
"""

from __future__ import annotations

import pytest

from services.ai_drafting.prompt_templates import (
    ALT_TEXT_SYSTEM_PROMPT,
    HEADING_HIERARCHY_SYSTEM_PROMPT,
    TABLE_STRUCTURE_SYSTEM_PROMPT,
    build_alt_text_prompt,
    build_heading_hierarchy_prompt,
    build_table_structure_prompt,
)


# ---------------------------------------------------------------------------
# build_alt_text_prompt — return shape and system instruction
# ---------------------------------------------------------------------------


def test_build_alt_text_prompt_returns_system_and_user():
    """build_alt_text_prompt returns a 2-tuple of (system, user) strings."""
    result = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[72, 144, 540, 360]",
        surrounding_text="The chart below illustrates budget allocation.",
        page_number=3,
    )
    assert isinstance(result, tuple)
    assert len(result) == 2
    system, user = result
    assert isinstance(system, str)
    assert isinstance(user, str)


def test_build_alt_text_prompt_system_is_constant_prompt():
    """The system instruction returned is always the module-level ALT_TEXT_SYSTEM_PROMPT."""
    system, _ = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 100, 100]",
        surrounding_text="Some context.",
        page_number=1,
    )
    assert system == ALT_TEXT_SYSTEM_PROMPT


def test_build_alt_text_prompt_includes_wcag_criterion():
    """The system instruction must reference WCAG 1.1.1 (Non-text Content)."""
    system, _ = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[72, 144, 540, 360]",
        surrounding_text="Figure caption: Annual revenue.",
        page_number=1,
    )
    assert "1.1.1" in system


def test_alt_text_prompt_includes_surrounding_text():
    """The user message must embed the supplied surrounding_text value."""
    surrounding = "The following figure shows the county seal and official motto."
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[10, 20, 200, 300]",
        surrounding_text=surrounding,
        page_number=2,
    )
    assert surrounding in user


def test_alt_text_prompt_includes_page_number():
    """The user message must include the 1-based page number."""
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[10, 20, 200, 300]",
        surrounding_text="context",
        page_number=7,
    )
    assert "7" in user


def test_alt_text_prompt_includes_bounding_box():
    """The user message must embed the bounding box string."""
    bbox = "[100, 200, 400, 500]"
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box=bbox,
        surrounding_text="context",
        page_number=1,
    )
    assert bbox in user


def test_alt_text_prompt_includes_element_type():
    """The user message must include the element type label."""
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 100, 100]",
        surrounding_text="context",
        page_number=1,
    )
    assert "Figure" in user


def test_alt_text_prompt_includes_additional_context_when_provided():
    """The user message must include the additional_context value when it is supplied."""
    context = "Figure 3: Departmental expenditure breakdown"
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 100, 100]",
        surrounding_text="surrounding",
        page_number=1,
        additional_context=context,
    )
    assert context in user


def test_alt_text_prompt_defaults_additional_context_when_empty():
    """When additional_context is empty or whitespace, the prompt uses 'None available'."""
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 100, 100]",
        surrounding_text="context",
        page_number=1,
        additional_context="   ",
    )
    assert "None available" in user


def test_alt_text_prompt_defaults_surrounding_text_when_empty():
    """When surrounding_text is whitespace only, the prompt substitutes 'None available'."""
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 100, 100]",
        surrounding_text="    ",
        page_number=1,
    )
    assert "None available" in user


def test_alt_text_prompt_includes_page_dimensions_when_provided():
    """Page dimensions are included in the user message when supplied."""
    _, user = build_alt_text_prompt(
        element_type="Figure",
        bounding_box="[0, 0, 612, 792]",
        surrounding_text="context",
        page_number=1,
        page_dimensions="612 x 792",
    )
    assert "612 x 792" in user


# ---------------------------------------------------------------------------
# build_table_structure_prompt — return shape and content
# ---------------------------------------------------------------------------


def test_build_table_structure_prompt_returns_system_and_user():
    """build_table_structure_prompt returns a 2-tuple of (system, user) strings."""
    result = build_table_structure_prompt(
        raw_table_data='[{"text": "Header A", "is_header": true}]',
        column_headers='["Header A"]',
        row_headers="[]",
    )
    assert isinstance(result, tuple)
    assert len(result) == 2
    system, user = result
    assert isinstance(system, str) and len(system) > 0
    assert isinstance(user, str) and len(user) > 0


def test_build_table_structure_prompt_system_is_constant_prompt():
    """The returned system instruction is always the module-level TABLE_STRUCTURE_SYSTEM_PROMPT."""
    system, _ = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
    )
    assert system == TABLE_STRUCTURE_SYSTEM_PROMPT


def test_build_table_structure_prompt_requests_json_output():
    """The system instruction must request JSON output (not HTML) for table analysis."""
    system, _ = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
    )
    assert "JSON" in system
    assert "header_row_count" in system
    assert "header_col_count" in system
    assert "suggested_caption" in system
    assert "has_merged_cells" in system


def test_build_table_structure_prompt_references_wcag_criterion():
    """The system instruction must reference WCAG 1.3.1 (Info and Relationships)."""
    system, _ = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
    )
    assert "1.3.1" in system


def test_build_table_structure_prompt_includes_raw_table_data():
    """The user message must embed the raw_table_data string."""
    raw = '[{"text": "Dept", "is_header": true, "row_index": 0, "col_index": 0}]'
    _, user = build_table_structure_prompt(
        raw_table_data=raw,
        column_headers='["Dept"]',
        row_headers="[]",
    )
    assert raw in user


def test_build_table_structure_prompt_includes_nesting_depth():
    """The user message must include the nesting_depth value."""
    _, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
        nesting_depth=2,
    )
    assert "2" in user


def test_table_prompt_handles_empty_headers():
    """build_table_structure_prompt must not raise when column_headers is empty/None."""
    system, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="",
        row_headers="",
    )
    # Verifies the function substitutes '[]' for empty headers
    assert "[]" in user


def test_table_prompt_includes_caption_text_when_provided():
    """The user message must embed the caption_text when it is non-empty."""
    caption = "Table 1: Department Headcount by Division, FY2025"
    _, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
        caption_text=caption,
    )
    assert caption in user


def test_table_prompt_defaults_caption_when_empty():
    """When caption_text is whitespace only, the prompt substitutes 'None available'."""
    _, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
        caption_text="   ",
    )
    assert "None available" in user


def test_table_prompt_includes_row_and_column_counts():
    """The user message must include the rows and cols values."""
    _, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
        rows=5,
        cols=3,
    )
    assert "5" in user
    assert "3" in user


def test_table_prompt_includes_table_id():
    """The user message must embed the table_id for traceability."""
    _, user = build_table_structure_prompt(
        raw_table_data="[]",
        column_headers="[]",
        row_headers="[]",
        table_id="tbl-element-007",
    )
    assert "tbl-element-007" in user


# ---------------------------------------------------------------------------
# build_heading_hierarchy_prompt — return shape and content
# ---------------------------------------------------------------------------


def test_build_heading_hierarchy_prompt_returns_system_and_user():
    """build_heading_hierarchy_prompt returns a 2-tuple of (system, user) strings."""
    result = build_heading_hierarchy_prompt(
        heading_list='[{"element_id": "h1", "page_number": 1, "level": 1, "text": "Title"}]',
        total_pages=10,
        document_title="Annual Report 2025",
    )
    assert isinstance(result, tuple)
    assert len(result) == 2
    system, user = result
    assert isinstance(system, str) and len(system) > 0
    assert isinstance(user, str) and len(user) > 0


def test_build_heading_hierarchy_prompt_system_is_constant_prompt():
    """The returned system instruction is always the module-level HEADING_HIERARCHY_SYSTEM_PROMPT."""
    system, _ = build_heading_hierarchy_prompt(heading_list="[]")
    assert system == HEADING_HIERARCHY_SYSTEM_PROMPT


def test_build_heading_hierarchy_prompt_returns_json_format_instruction():
    """The system instruction must instruct the model to return a JSON array."""
    system, _ = build_heading_hierarchy_prompt(heading_list="[]")
    assert "JSON array" in system or "JSON" in system


def test_heading_prompt_includes_all_heading_fields():
    """The system instruction must specify all required output fields for each heading."""
    system, _ = build_heading_hierarchy_prompt(heading_list="[]")
    required_fields = [
        "original_level",
        "corrected_level",
        "text",
        "element_id",
        "page_number",
        "flag",
        "suggestion",
    ]
    for field_name in required_fields:
        assert field_name in system, f"Expected field '{field_name}' in system prompt"


def test_heading_prompt_includes_wcag_criterion():
    """The system instruction must reference WCAG 2.4.6 (Headings and Labels)."""
    system, _ = build_heading_hierarchy_prompt(heading_list="[]")
    assert "2.4.6" in system


def test_heading_prompt_includes_heading_list():
    """The user message must embed the heading_list JSON string."""
    headings = '[{"element_id": "e1", "page_number": 1, "level": 2, "text": "Introduction"}]'
    _, user = build_heading_hierarchy_prompt(heading_list=headings)
    assert headings in user


def test_heading_prompt_includes_document_title():
    """The user message must include the document_title when provided."""
    _, user = build_heading_hierarchy_prompt(
        heading_list="[]",
        document_title="Sacramento County Master Plan 2030",
    )
    assert "Sacramento County Master Plan 2030" in user


def test_heading_prompt_includes_total_pages():
    """The user message must include the total_pages count."""
    _, user = build_heading_hierarchy_prompt(heading_list="[]", total_pages=42)
    assert "42" in user


def test_heading_prompt_defaults_empty_heading_list():
    """When heading_list is empty, the user message uses '[]' as a safe default."""
    _, user = build_heading_hierarchy_prompt(heading_list="")
    assert "[]" in user


def test_heading_prompt_defaults_document_title_when_none():
    """When document_title is falsy, the prompt substitutes 'Unknown'."""
    _, user = build_heading_hierarchy_prompt(heading_list="[]", document_title="")
    assert "Unknown" in user


def test_heading_prompt_specifies_valid_flag_values():
    """The system instruction must enumerate all valid flag values: OK, LEVEL_CORRECTED, NEEDS_REVIEW, MANUAL."""
    system, _ = build_heading_hierarchy_prompt(heading_list="[]")
    for flag in ("OK", "LEVEL_CORRECTED", "NEEDS_REVIEW", "MANUAL"):
        assert flag in system, f"Expected flag value '{flag}' in system prompt"


# ---------------------------------------------------------------------------
# generate_alt_text_for_image — unit tests (no real Vertex AI calls)
# ---------------------------------------------------------------------------


def test_generate_alt_text_returns_fallback_when_model_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When vertex_ai_model is empty, the function must return the fallback alt text."""
    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "")
    # GOOGLE_APPLICATION_CREDENTIALS presence does not matter here —
    # the model check fires first.
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    result = vertex_client.generate_alt_text_for_image(
        image_base64="aW1hZ2VkYXRh",  # base64 "imagedata"
        image_mime="image/png",
        surrounding_text="A chart showing budget allocation.",
        page_num=1,
        fallback_alt="[Figure on page 1 — alt text requires review]",
    )
    assert result == "[Figure on page 1 — alt text requires review]"


def test_generate_alt_text_returns_fallback_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GOOGLE_APPLICATION_CREDENTIALS is unset, the function returns the fallback."""
    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    result = vertex_client.generate_alt_text_for_image(
        image_base64="aW1hZ2VkYXRh",
        image_mime="image/png",
        surrounding_text="Some surrounding text.",
        page_num=2,
        fallback_alt="[Figure on page 2 — alt text requires review]",
    )
    assert result == "[Figure on page 2 — alt text requires review]"


def test_generate_alt_text_returns_fallback_when_image_base64_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When image_base64 is an empty string, the function returns the fallback immediately."""
    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    result = vertex_client.generate_alt_text_for_image(
        image_base64="",
        image_mime="image/png",
        surrounding_text="Some context.",
        page_num=3,
        fallback_alt="[Figure on page 3 — alt text requires review]",
    )
    assert result == "[Figure on page 3 — alt text requires review]"


def test_generate_alt_text_returns_fallback_when_vertex_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Vertex AI call raises any exception, the fallback is returned — not the exception."""
    from unittest.mock import MagicMock
    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    # Make the SDK appear available so we get past Gate 0
    monkeypatch.setattr(vertex_client, "_VERTEXAI_AVAILABLE", True)

    # Patch vertexai (the module-level name) with a mock whose .init raises
    mock_vertexai = MagicMock()
    mock_vertexai.init.side_effect = RuntimeError("Simulated GCP credential error")
    monkeypatch.setattr(vertex_client, "vertexai", mock_vertexai)

    result = vertex_client.generate_alt_text_for_image(
        image_base64="aW1hZ2VkYXRh",
        image_mime="image/png",
        surrounding_text="Surrounding text context.",
        page_num=5,
        fallback_alt="[Figure on page 5 — alt text requires review]",
    )
    assert result == "[Figure on page 5 — alt text requires review]"


def _setup_vertex_mocks(monkeypatch: pytest.MonkeyPatch, response_text: str):
    """Helper: configure all Vertex AI module-level names as mocks.

    Because the vertexai SDK is not installed in the test environment, all
    module-level names (vertexai, GenerativeModel, Part, Image, …) are None.
    This helper patches them so tests can exercise the Gemini call path.

    Returns the mock model so callers can inspect calls if needed.
    """
    from unittest.mock import MagicMock
    from services.ai_drafting import vertex_client

    # Override the availability flag so Gate 0 passes
    monkeypatch.setattr(vertex_client, "_VERTEXAI_AVAILABLE", True)

    # Mock vertexai module-level object (provides .init)
    mock_vertexai = MagicMock()
    monkeypatch.setattr(vertex_client, "vertexai", mock_vertexai)

    # Mock Part and Image so Part.from_image / Image.from_bytes don't fail
    mock_image_cls = MagicMock()
    mock_part_cls = MagicMock()
    monkeypatch.setattr(vertex_client, "Image", mock_image_cls)
    monkeypatch.setattr(vertex_client, "Part", mock_part_cls)

    # Build a fake response
    mock_response_part = MagicMock()
    mock_response_part.text = response_text
    mock_content = MagicMock()
    mock_content.parts = [mock_response_part]
    mock_candidate = MagicMock()
    mock_candidate.content = mock_content
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_generative_model_cls = MagicMock(return_value=mock_model)
    monkeypatch.setattr(vertex_client, "GenerativeModel", mock_generative_model_cls)

    # Mock GenerationConfig
    monkeypatch.setattr(vertex_client, "GenerationConfig", MagicMock())

    return mock_model


def test_generate_alt_text_returns_empty_string_for_decorative_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Gemini returns the decorative marker, the function returns an empty string."""
    import base64

    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    # Gemini returns the two-character decorative marker (quoted)
    _setup_vertex_mocks(monkeypatch, response_text='""')

    tiny_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

    result = vertex_client.generate_alt_text_for_image(
        image_base64=tiny_png_b64,
        image_mime="image/png",
        surrounding_text="Decorative border image.",
        page_num=1,
        fallback_alt="[Figure on page 1 — alt text requires review]",
    )
    # The result should be empty string (decorative), not the fallback
    assert result == ""


def test_generate_alt_text_returns_generated_text_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Gemini returns valid alt text, the function returns it directly."""
    import base64

    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    expected_alt = "Bar chart showing annual budget allocation by department, 2020-2024."
    # Gemini sometimes wraps in quotes — the function must strip them
    _setup_vertex_mocks(monkeypatch, response_text=f'"{expected_alt}"')

    tiny_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

    result = vertex_client.generate_alt_text_for_image(
        image_base64=tiny_png_b64,
        image_mime="image/png",
        surrounding_text="Department budget summary for fiscal year 2024.",
        page_num=3,
        fallback_alt="[Figure on page 3 — alt text requires review]",
    )
    assert result == expected_alt


def test_generate_alt_text_truncates_runaway_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Responses longer than 1000 chars must be truncated to exactly 1000 chars."""
    import base64

    from services.ai_drafting import vertex_client
    from services.common.config import settings

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    # Generate a 1500-char response
    long_text = "A" * 1500
    _setup_vertex_mocks(monkeypatch, response_text=long_text)

    tiny_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

    result = vertex_client.generate_alt_text_for_image(
        image_base64=tiny_png_b64,
        image_mime="image/png",
        surrounding_text="context",
        page_num=1,
        fallback_alt="[Figure on page 1 — alt text requires review]",
    )
    assert len(result) == 1000


# ---------------------------------------------------------------------------
# stage_ai_alt_text — unit tests (no real Vertex AI calls)
# ---------------------------------------------------------------------------


def test_stage_ai_alt_text_skips_when_vertex_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stage_ai_alt_text raises StageNoOpError when Vertex AI is not configured."""
    from services.common.config import settings
    from services.common.ir import BlockSource, BlockType, IRBlock, IRDocument, IRPage, RemediationStatus
    from services.common.pipeline import StageNoOpError
    from services.ingestion.converter import stage_ai_alt_text

    monkeypatch.setattr(settings, "vertex_ai_model", "")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    # Build an IR doc with one image block that has a generic placeholder
    block = IRBlock(
        block_type=BlockType.IMAGE,
        source=BlockSource.ADOBE,
        remediation_status=RemediationStatus.RAW,
        attributes={
            "alt": "[Figure on page 1 — alt text requires review]",
            "src": "data:image/png;base64,aW1hZ2U=",
        },
    )
    ir_doc = IRDocument(
        document_id="test-skip-1",
        filename="test.pdf",
        page_count=1,
        pages=[IRPage(page_num=0, blocks=[block])],
    )

    # Now raises StageNoOpError instead of silently returning —
    # run_stage catches this and marks the stage as "degraded"
    with pytest.raises(StageNoOpError) as exc_info:
        stage_ai_alt_text(ir_doc)

    # The ir_doc is attached to the exception so run_stage can extract it
    assert exc_info.value.data is ir_doc  # type: ignore[attr-defined]
    # The alt text must be unchanged — Vertex AI was not called
    assert ir_doc.all_blocks()[0].attributes["alt"] == "[Figure on page 1 — alt text requires review]"
    assert ir_doc.all_blocks()[0].remediation_status == RemediationStatus.RAW


def test_stage_ai_alt_text_skips_images_with_real_alt_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stage_ai_alt_text must not touch IMAGE blocks that already have non-generic alt text."""
    from unittest.mock import MagicMock
    from services.common.config import settings
    from services.common.ir import BlockSource, BlockType, IRBlock, IRDocument, IRPage, RemediationStatus
    from services.ingestion import converter

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    # Patch the generate function so we can detect if it was called
    mock_generate = MagicMock(return_value="should not be used")
    monkeypatch.setattr(
        "services.ai_drafting.vertex_client.generate_alt_text_for_image",
        mock_generate,
    )
    # Also patch the import inside converter
    monkeypatch.setattr(converter, "_vertex_ai_available", lambda: True)

    block = IRBlock(
        block_type=BlockType.IMAGE,
        source=BlockSource.ADOBE,
        remediation_status=RemediationStatus.RAW,
        attributes={
            "alt": "Sacramento County seal",  # Real alt text from Adobe
            "src": "data:image/png;base64,aW1hZ2U=",
        },
    )
    ir_doc = IRDocument(
        document_id="test-skip-real-alt",
        filename="test.pdf",
        page_count=1,
        pages=[IRPage(page_num=0, blocks=[block])],
    )

    from services.ingestion.converter import stage_ai_alt_text

    result, metrics = stage_ai_alt_text(ir_doc)

    # Alt text must remain unchanged
    assert result.all_blocks()[0].attributes["alt"] == "Sacramento County seal"
    # The generate function should NOT have been called
    mock_generate.assert_not_called()
    assert metrics["ai_attempted"] == 0


def test_stage_ai_alt_text_skips_images_without_src(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IMAGE blocks without a base64 src must be skipped (no image data to send to Gemini)."""
    from unittest.mock import MagicMock
    from services.common.config import settings
    from services.common.ir import BlockSource, BlockType, IRBlock, IRDocument, IRPage, RemediationStatus
    from services.ingestion import converter

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")

    mock_generate = MagicMock(return_value="should not be used")
    monkeypatch.setattr(converter, "_vertex_ai_available", lambda: True)

    block = IRBlock(
        block_type=BlockType.IMAGE,
        source=BlockSource.ADOBE,
        remediation_status=RemediationStatus.RAW,
        attributes={
            "alt": "[Figure on page 2 — alt text requires review]",
            # No "src" key — image bytes were not extracted
        },
    )
    ir_doc = IRDocument(
        document_id="test-skip-no-src",
        filename="test.pdf",
        page_count=1,
        pages=[IRPage(page_num=0, blocks=[block])],
    )

    from services.ingestion.converter import stage_ai_alt_text

    result, metrics = stage_ai_alt_text(ir_doc)

    # Alt text must remain unchanged since there is no image data
    assert result.all_blocks()[0].attributes["alt"] == "[Figure on page 2 — alt text requires review]"
    assert metrics["skipped_no_src"] >= 1


def test_stage_ai_alt_text_processes_all_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All eligible images must be processed — there is no per-document cap."""
    import base64
    from unittest.mock import patch
    from services.common.config import settings
    from services.common.ir import BlockSource, BlockType, IRBlock, IRDocument, IRPage, RemediationStatus
    from services.ingestion import converter

    monkeypatch.setattr(settings, "vertex_ai_model", "gemini-2.5-pro")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json")
    monkeypatch.setattr(converter, "_vertex_ai_available", lambda: True)

    # Patch time.sleep so batch rate-limit delays don't slow down the test
    monkeypatch.setattr(converter.time, "sleep", lambda _: None)

    generated_alt = "Generated alt text for image."
    call_count = 0

    def _fake_generate(image_base64, image_mime, surrounding_text, page_num, fallback_alt):
        nonlocal call_count
        call_count += 1
        return generated_alt

    tiny_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
    placeholder = "[Figure on page 1 — alt text requires review]"

    # Use more images than the old cap of 20 to verify unlimited processing
    num_images = 7
    blocks = [
        IRBlock(
            block_type=BlockType.IMAGE,
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
            attributes={"alt": placeholder, "src": f"data:image/png;base64,{tiny_b64}"},
        )
        for _ in range(num_images)
    ]

    ir_doc = IRDocument(
        document_id="test-unlimited",
        filename="test.pdf",
        page_count=1,
        pages=[IRPage(page_num=0, blocks=blocks)],
    )

    # Patch the function in its defining module — converter imports it lazily
    # from vertex_client, so we patch it at the source.
    with patch(
        "services.ai_drafting.vertex_client.generate_alt_text_for_image",
        side_effect=_fake_generate,
    ):
        from services.ingestion.converter import stage_ai_alt_text
        result, metrics = stage_ai_alt_text(ir_doc)

    all_blocks = result.all_blocks()
    # ALL blocks should have AI-generated alt text — no cap
    for i in range(num_images):
        assert all_blocks[i].attributes["alt"] == generated_alt, (
            f"Block {i} should have AI alt text (no cap)"
        )
    assert call_count == num_images, (
        f"Expected {num_images} Gemini calls (all images), got {call_count}"
    )


def test_get_surrounding_text_collects_adjacent_paragraphs() -> None:
    """_get_surrounding_text collects text from before and after the image block."""
    from services.common.ir import BlockSource, BlockType, IRBlock, IRPage, RemediationStatus
    from services.ingestion.converter import _get_surrounding_text

    blocks = [
        IRBlock(
            block_type=BlockType.PARAGRAPH,
            content="First paragraph before image.",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
        ),
        IRBlock(
            block_type=BlockType.PARAGRAPH,
            content="Second paragraph before image.",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
        ),
        IRBlock(
            block_type=BlockType.IMAGE,
            content="",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
            attributes={"alt": "[Figure]", "src": "data:image/png;base64,x"},
        ),
        IRBlock(
            block_type=BlockType.PARAGRAPH,
            content="Paragraph after image.",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
        ),
    ]

    result = _get_surrounding_text(blocks, image_idx=2, window=3)
    assert "First paragraph before image." in result
    assert "Second paragraph before image." in result
    assert "Paragraph after image." in result


def test_get_surrounding_text_excludes_image_blocks() -> None:
    """_get_surrounding_text must not include content from other IMAGE blocks."""
    from services.common.ir import BlockSource, BlockType, IRBlock, RemediationStatus
    from services.ingestion.converter import _get_surrounding_text

    blocks = [
        IRBlock(
            block_type=BlockType.IMAGE,
            content="",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
            attributes={"alt": "[Figure A]"},
        ),
        IRBlock(
            block_type=BlockType.IMAGE,
            content="",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
            attributes={"alt": "[Figure B]"},
        ),
    ]

    # Requesting surrounding text for the second image
    result = _get_surrounding_text(blocks, image_idx=1, window=3)
    # Should be empty — the only adjacent block is another image
    assert result == ""


def test_get_surrounding_text_respects_window_limit() -> None:
    """_get_surrounding_text must collect at most ``window`` blocks in each direction."""
    from services.common.ir import BlockSource, BlockType, IRBlock, RemediationStatus
    from services.ingestion.converter import _get_surrounding_text

    # Build 10 paragraphs, then an image at index 10
    blocks = [
        IRBlock(
            block_type=BlockType.PARAGRAPH,
            content=f"Paragraph {i}.",
            source=BlockSource.ADOBE,
            remediation_status=RemediationStatus.RAW,
        )
        for i in range(10)
    ]
    image_block = IRBlock(
        block_type=BlockType.IMAGE,
        content="",
        source=BlockSource.ADOBE,
        remediation_status=RemediationStatus.RAW,
        attributes={"alt": "[Figure]"},
    )
    blocks.append(image_block)

    result = _get_surrounding_text(blocks, image_idx=10, window=2)
    # Should include only paragraphs 8 and 9 (the 2 immediately before the image)
    assert "Paragraph 8." in result
    assert "Paragraph 9." in result
    # Paragraphs further away should NOT be included
    assert "Paragraph 0." not in result
    assert "Paragraph 7." not in result
