"""Tests for the axesSense REST API client."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from services.common.axessense_client import (
    AxesSenseClient,
    AxesSenseResult,
    MatterhornRuleResult,
)


class TestAxesSenseClientInit:
    """Construction and configuration."""

    def test_init_with_explicit_params(self):
        client = AxesSenseClient(base_url="https://api.example.com", api_key="key123")
        assert client._base_url == "https://api.example.com"
        assert client._api_key == "key123"

    def test_init_strips_trailing_slash(self):
        client = AxesSenseClient(base_url="https://api.example.com/", api_key="k")
        assert client._base_url == "https://api.example.com"

    def test_init_with_empty_params(self):
        client = AxesSenseClient(base_url="", api_key="")
        assert client._base_url == ""
        assert client._api_key == ""


class TestIsAvailable:
    """Health check availability."""

    def test_returns_false_when_not_configured(self):
        client = AxesSenseClient(base_url="", api_key="")
        assert client.is_available() is False

    def test_returns_false_when_no_api_key(self):
        client = AxesSenseClient(base_url="https://api.example.com", api_key="")
        assert client.is_available() is False

    @patch("services.common.axessense_client.httpx.get")
    def test_returns_true_on_healthy_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        assert client.is_available() is True

    @patch("services.common.axessense_client.httpx.get")
    def test_returns_false_on_server_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        assert client.is_available() is False

    @patch("services.common.axessense_client.httpx.get")
    def test_returns_false_on_connection_error(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.ConnectError("refused")
        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        assert client.is_available() is False


class TestValidateMatterhorn:
    """Matterhorn Protocol validation."""

    def test_returns_none_when_not_configured(self):
        client = AxesSenseClient(base_url="", api_key="")
        result = client.validate_matterhorn(b"%PDF-1.7 test")
        assert result is None

    @patch("services.common.axessense_client.httpx.post")
    def test_returns_parsed_result_on_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "compliant": True,
            "checks": [
                {
                    "rule_id": "01-003",
                    "clause": "7.1",
                    "description": "Document tagged",
                    "status": "pass",
                    "failure_count": 0,
                },
                {
                    "rule_id": "01-004",
                    "clause": "7.1",
                    "description": "Artifacts not tagged",
                    "status": "fail",
                    "failure_count": 3,
                    "details": ["page 1", "page 2", "page 5"],
                },
            ],
        }
        mock_post.return_value = mock_resp

        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        result = client.validate_matterhorn(b"%PDF-1.7 test")

        assert result is not None
        assert result.is_compliant is True
        assert result.total_checks == 2
        assert result.passed_checks == 1
        assert result.failed_checks == 1
        assert len(result.rule_results) == 2
        assert result.rule_results[0].rule_id == "01-003"
        assert result.rule_results[0].status == "pass"
        assert result.rule_results[1].status == "fail"
        assert result.rule_results[1].failure_count == 3

    @patch("services.common.axessense_client.httpx.post")
    def test_returns_none_on_non_200(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = "Invalid PDF"
        mock_post.return_value = mock_resp

        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        result = client.validate_matterhorn(b"%PDF-1.7 test")
        assert result is None

    @patch("services.common.axessense_client.httpx.post")
    def test_returns_none_on_timeout(self, mock_post):
        import httpx
        mock_post.side_effect = httpx.TimeoutException("read timeout")

        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        result = client.validate_matterhorn(b"%PDF-1.7 test")
        assert result is None

    @patch("services.common.axessense_client.httpx.post")
    def test_handles_alternative_response_format(self, mock_post):
        """axesSense may use 'rules' instead of 'checks'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "is_compliant": False,
            "rules": [
                {
                    "id": "06-001",
                    "message": "Missing alt text",
                    "result": "FAIL",
                    "count": 5,
                },
            ],
        }
        mock_post.return_value = mock_resp

        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        result = client.validate_matterhorn(b"%PDF-1.7 test")

        assert result is not None
        assert result.is_compliant is False
        assert result.failed_checks == 1
        assert result.rule_results[0].rule_id == "06-001"
        assert result.rule_results[0].description == "Missing alt text"

    @patch("services.common.axessense_client.httpx.post")
    def test_handles_not_applicable_status(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "checks": [
                {"rule_id": "11-001", "status": "not_applicable"},
                {"rule_id": "11-002", "status": "na"},
                {"rule_id": "11-003", "status": "skip"},
            ],
        }
        mock_post.return_value = mock_resp

        client = AxesSenseClient(base_url="https://api.example.com", api_key="key")
        result = client.validate_matterhorn(b"%PDF-1.7 test")

        assert result is not None
        assert result.not_applicable_checks == 3
        assert result.passed_checks == 0
        assert result.failed_checks == 0
        assert result.is_compliant is True  # No failures = compliant


class TestParseResponse:
    """Response parsing edge cases."""

    def test_empty_checks_list(self):
        client = AxesSenseClient(base_url="https://x.com", api_key="k")
        result = client._parse_response({"checks": []})
        assert result.total_checks == 0
        assert result.is_compliant is True

    def test_non_dict_check_items_skipped(self):
        client = AxesSenseClient(base_url="https://x.com", api_key="k")
        result = client._parse_response({"checks": ["invalid", 42, None]})
        assert result.total_checks == 0

    def test_missing_keys_use_defaults(self):
        client = AxesSenseClient(base_url="https://x.com", api_key="k")
        result = client._parse_response({"checks": [{}]})
        assert result.total_checks == 0  # Empty status doesn't count
        assert len(result.rule_results) == 1
        assert result.rule_results[0].rule_id == ""
        assert result.rule_results[0].status == ""
