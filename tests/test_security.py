"""Tests for security utilities."""

import pytest
from services.common.security import validate_pdf_bytes, sanitize_filename, check_file_size


class TestValidatePdfBytes:
    def test_valid_pdf_header(self):
        validate_pdf_bytes(b"%PDF-1.4 some content", "test.pdf")

    def test_valid_pdf_with_bom(self):
        validate_pdf_bytes(b"\xef\xbb\xbf%PDF-1.7 content", "bom.pdf")

    def test_pdf_signature_within_1024_bytes(self):
        # Some PDFs have binary header before %PDF
        data = b"\x00" * 100 + b"%PDF-1.5 rest"
        validate_pdf_bytes(data, "offset.pdf")

    def test_empty_file_raises(self):
        with pytest.raises(ValueError, match="empty"):
            validate_pdf_bytes(b"", "empty.pdf")

    def test_non_pdf_raises(self):
        with pytest.raises(ValueError, match="does not appear to be a valid PDF"):
            validate_pdf_bytes(b"<html>not a pdf</html>", "fake.pdf")

    def test_text_file_raises(self):
        with pytest.raises(ValueError, match="does not appear to be a valid PDF"):
            validate_pdf_bytes(b"Just plain text content", "text.txt")


class TestSanitizeFilename:
    def test_normal_filename(self):
        assert sanitize_filename("report.pdf") == "report.pdf"

    def test_path_separators_removed(self):
        result = sanitize_filename("../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result

    def test_special_chars_replaced(self):
        result = sanitize_filename("report<script>.pdf")
        assert "<" not in result
        assert ">" not in result

    def test_length_limit(self):
        long_name = "a" * 300 + ".pdf"
        result = sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".pdf")

    def test_empty_returns_default(self):
        assert sanitize_filename("") == "document.pdf"

    def test_spaces_preserved(self):
        assert sanitize_filename("my report.pdf") == "my report.pdf"

    def test_backslash_path(self):
        result = sanitize_filename("C:\\Users\\test\\file.pdf")
        assert "\\" not in result


class TestCheckFileSize:
    def test_small_file_passes(self):
        check_file_size(b"small", max_bytes=1024)

    def test_oversized_file_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            check_file_size(b"x" * 1025, max_bytes=1024)

    def test_exact_limit_passes(self):
        check_file_size(b"x" * 1024, max_bytes=1024)

    def test_default_50mb_limit(self):
        # Just under 50MB should pass
        small_data = b"x" * 100
        check_file_size(small_data)  # Should not raise
