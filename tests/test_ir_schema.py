"""Tests for the Intermediate Representation (IR) schema models."""

from __future__ import annotations

import json

import pytest

from services.common.ir import (
    BlockSource,
    BlockType,
    BoundingBox,
    IRBlock,
    IRDocument,
    IRPage,
    RemediationStatus,
)


# ---------------------------------------------------------------------------
# BoundingBox
# ---------------------------------------------------------------------------


def test_bounding_box_defaults():
    bbox = BoundingBox()
    assert bbox.x1 == 0.0
    assert bbox.y1 == 0.0
    assert bbox.x2 == 0.0
    assert bbox.y2 == 0.0


def test_bounding_box_custom_values():
    bbox = BoundingBox(x1=0.1, y1=0.2, x2=0.9, y2=0.8)
    assert bbox.x1 == 0.1
    assert bbox.x2 == 0.9


# ---------------------------------------------------------------------------
# IRBlock
# ---------------------------------------------------------------------------


def test_irblock_auto_generates_id():
    block = IRBlock(block_type=BlockType.PARAGRAPH, content="Hello")
    assert block.block_id != ""
    assert len(block.block_id) > 8  # UUID format


def test_irblock_default_values():
    block = IRBlock(block_type=BlockType.HEADING, content="Title")
    assert block.confidence == 1.0
    assert block.source == BlockSource.ADOBE
    assert block.remediation_status == RemediationStatus.RAW
    assert block.wcag_criteria == []
    assert block.attributes == {}


def test_irblock_with_attributes():
    block = IRBlock(
        block_type=BlockType.TABLE,
        content="Budget",
        attributes={"headers": ["A", "B"], "rows": [["1", "2"]]},
    )
    assert block.attributes["headers"] == ["A", "B"]


def test_irblock_all_block_types():
    for bt in BlockType:
        block = IRBlock(block_type=bt, content="test")
        assert block.block_type == bt


def test_irblock_all_sources():
    for src in BlockSource:
        block = IRBlock(block_type=BlockType.PARAGRAPH, content="test", source=src)
        assert block.source == src


# ---------------------------------------------------------------------------
# IRPage
# ---------------------------------------------------------------------------


def test_irpage_defaults():
    page = IRPage(page_num=0, width=612, height=792)
    assert page.blocks == []
    assert page.extraction_method == "adobe"


def test_irpage_with_blocks():
    b1 = IRBlock(block_type=BlockType.HEADING, content="Title")
    b2 = IRBlock(block_type=BlockType.PARAGRAPH, content="Body")
    page = IRPage(page_num=0, width=612, height=792, blocks=[b1, b2])
    assert len(page.blocks) == 2


# ---------------------------------------------------------------------------
# IRDocument
# ---------------------------------------------------------------------------


def test_irdocument_creation():
    doc = IRDocument(
        document_id="test-123",
        filename="test.pdf",
        page_count=2,
    )
    assert doc.document_id == "test-123"
    assert doc.pages == []
    assert doc.language == "en"


def test_irdocument_all_blocks():
    b1 = IRBlock(block_type=BlockType.HEADING, content="H1")
    b2 = IRBlock(block_type=BlockType.PARAGRAPH, content="P1")
    b3 = IRBlock(block_type=BlockType.IMAGE, content="", attributes={"alt": "img"})

    page1 = IRPage(page_num=0, width=612, height=792, blocks=[b1, b2])
    page2 = IRPage(page_num=1, width=612, height=792, blocks=[b3])

    doc = IRDocument(
        document_id="test-456",
        filename="test.pdf",
        page_count=2,
        pages=[page1, page2],
    )

    all_blocks = doc.all_blocks()
    assert len(all_blocks) == 3


def test_irdocument_blocks_by_type():
    b1 = IRBlock(block_type=BlockType.HEADING, content="H1")
    b2 = IRBlock(block_type=BlockType.PARAGRAPH, content="P1")
    b3 = IRBlock(block_type=BlockType.HEADING, content="H2")

    page = IRPage(page_num=0, width=612, height=792, blocks=[b1, b2, b3])
    doc = IRDocument(
        document_id="test-789",
        filename="test.pdf",
        page_count=1,
        pages=[page],
    )

    headings = doc.blocks_by_type(BlockType.HEADING)
    assert len(headings) == 2
    assert all(b.block_type == BlockType.HEADING for b in headings)


def test_irdocument_to_legacy_elements():
    b1 = IRBlock(block_type=BlockType.HEADING, content="Title", attributes={"level": 1})
    b2 = IRBlock(block_type=BlockType.PARAGRAPH, content="Body text")
    b3 = IRBlock(
        block_type=BlockType.TABLE,
        content="Budget",
        attributes={"headers": ["A"], "rows": [["1"]]},
    )

    page = IRPage(page_num=0, width=612, height=792, blocks=[b1, b2, b3])
    doc = IRDocument(
        document_id="legacy-test",
        filename="test.pdf",
        page_count=1,
        pages=[page],
    )

    elements = doc.to_legacy_elements()
    assert len(elements) == 3
    assert elements[0]["type"] == "heading"
    assert elements[0]["content"] == "Title"
    assert elements[0]["attributes"]["level"] == 1
    assert elements[1]["type"] == "paragraph"
    assert elements[2]["type"] == "table"


def test_irdocument_json_roundtrip():
    b1 = IRBlock(block_type=BlockType.PARAGRAPH, content="Test")
    page = IRPage(page_num=0, width=612, height=792, blocks=[b1])
    doc = IRDocument(
        document_id="json-test",
        filename="test.pdf",
        page_count=1,
        pages=[page],
    )

    json_str = doc.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["document_id"] == "json-test"
    assert len(parsed["pages"]) == 1
    assert len(parsed["pages"][0]["blocks"]) == 1
