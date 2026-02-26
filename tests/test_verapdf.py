"""Comprehensive tests for VeraPDF validation feature.

Tests cover:
    - VeraPDFClient.is_available() — healthy and unreachable container
    - VeraPDFClient.validate_pdfua1() — compliant and non-compliant results,
      connection failures, non-200 HTTP responses
    - VeraPDFResult / VeraPDFRuleFailure model population
    - run_gate_g4_verapdf() — unavailable container (soft-fail P2),
      non-compliant result (P1 per clause), compliant result (all pass),
      endline improvement and regression delta checks

All external I/O (httpx) is replaced with unittest.mock.patch so that no
real VeraPDF container is required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.common.verapdf_client import (
    VeraPDFClient,
    VeraPDFResult,
    VeraPDFRuleFailure,
)
from services.common.gates import run_gate_g4_verapdf


# ---------------------------------------------------------------------------
# Realistic VeraPDF REST API response fixtures
# ---------------------------------------------------------------------------

# Direct-format response (compliant)
_DIRECT_COMPLIANT: dict[str, Any] = {
    "compliant": True,
    "ruleSummaries": [
        {
            "ruleId": "UA1:7.1-t01",
            "clause": "7.1",
            "description": "Document title in metadata",
            "failedChecks": 0,
        },
        {
            "ruleId": "UA1:7.18.1-t01",
            "clause": "7.18.1",
            "description": "Alt text on figures",
            "failedChecks": 0,
        },
    ],
}

# Direct-format response (non-compliant: two failing rules, two passing rules)
_DIRECT_NONCOMPLIANT: dict[str, Any] = {
    "compliant": False,
    "ruleSummaries": [
        {
            "ruleId": "UA1:7.1-t01",
            "clause": "7.1",
            "description": "Document title in metadata",
            "failedChecks": 0,
        },
        {
            "ruleId": "UA1:7.18.1-t01",
            "clause": "7.18.1",
            "description": "Alt text on figures",
            "failedChecks": 3,
        },
        {
            "ruleId": "UA1:7.4-t01",
            "clause": "7.4",
            "description": "PDF/UA identifier in metadata",
            "failedChecks": 1,
        },
        {
            "ruleId": "UA1:7.2-t01",
            "clause": "7.2",
            "description": "Headings properly tagged",
            "failedChecks": 0,
        },
    ],
}

# Nested report-format response (compliant)
_REPORT_COMPLIANT: dict[str, Any] = {
    "report": {
        "jobs": [
            {
                "validationResult": {
                    "compliant": True,
                    "details": {
                        "rules": [
                            {
                                "status": "passed",
                                "clause": "7.1",
                                "description": "Document title",
                                "ruleId": "UA1:7.1-t01",
                            },
                        ]
                    },
                }
            }
        ]
    }
}

# Nested report-format response (non-compliant: one failing rule)
_REPORT_NONCOMPLIANT: dict[str, Any] = {
    "report": {
        "jobs": [
            {
                "validationResult": {
                    "compliant": False,
                    "details": {
                        "rules": [
                            {
                                "status": "failed",
                                "clause": "7.18.1",
                                "description": "Alt text on figures",
                                "testNumber": 1,
                                "failedChecks": 5,
                                "ruleId": {"id": "UA1:7.18.1-t01"},
                            },
                            {
                                "status": "passed",
                                "clause": "7.1",
                                "description": "Document title",
                                "ruleId": "UA1:7.1-t01",
                            },
                        ]
                    },
                }
            }
        ]
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_PDF = b"%PDF-1.7 dummy content"


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(json_data) if json_data else ""
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# VeraPDFClient.is_available()
# ---------------------------------------------------------------------------


class TestVeraPDFClientIsAvailable:
    """Tests for the health-check probe."""

    def test_client_available_returns_true(self):
        """When the /api/info endpoint responds with HTTP 200, is_available is True."""
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(status_code=200)
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is True
            mock_get.assert_called_once()
            called_url = mock_get.call_args[0][0]
            assert "/api/info" in called_url

    def test_client_unavailable_with_non_200_status(self):
        """Status codes other than 200 (e.g. 404) mean the container is not properly configured."""
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(status_code=404)
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is False

    def test_client_unavailable_with_401_status(self):
        """HTTP 401 means authentication failed — container not usable."""
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _mock_response(status_code=401)
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is False

    def test_client_unavailable_returns_false_on_connection_error(self):
        """ConnectError from httpx.get causes is_available to return False."""
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is False

    def test_client_unavailable_returns_false_on_timeout(self):
        """TimeoutException from httpx.get causes is_available to return False."""
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timed out")
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is False

    def test_client_unavailable_returns_false_on_oserror(self):
        """OSError (e.g. network down) causes is_available to return False."""
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = OSError("Network unreachable")
            client = VeraPDFClient(base_url="http://localhost:8080")
            assert client.is_available() is False


# ---------------------------------------------------------------------------
# VeraPDFClient.validate_pdfua1() — connection / HTTP failures
# ---------------------------------------------------------------------------


class TestVeraPDFClientValidatePdfua1Failures:
    """validate_pdfua1 must return None gracefully on any I/O or HTTP failure."""

    def test_client_unavailable_returns_none_on_connect_error(self):
        """ConnectError during POST returns None (no exception propagated)."""
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)
            assert result is None

    def test_client_unavailable_returns_none_on_timeout(self):
        """TimeoutException during POST returns None."""
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Timed out")
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)
            assert result is None

    def test_non_200_5xx_retries_then_returns_none(self):
        """HTTP 500 from the validation endpoint is retried then returns None."""
        with patch("httpx.post") as mock_post, \
             patch("services.common.verapdf_client.time.sleep"):
            mock_post.return_value = _mock_response(status_code=500)
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)
            assert result is None
            # Should have been called 3 times (1 initial + 2 retries)
            assert mock_post.call_count == 3

    def test_non_200_5xx_succeeds_on_retry(self):
        """HTTP 500 followed by 200 on retry returns a valid result."""
        with patch("httpx.post") as mock_post, \
             patch("services.common.verapdf_client.time.sleep"):
            mock_post.side_effect = [
                _mock_response(status_code=500),
                _mock_response(status_code=200, json_data=_DIRECT_COMPLIANT),
            ]
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)
            assert result is not None
            assert result.is_compliant is True
            assert mock_post.call_count == 2

    def test_non_200_422_response_returns_none_no_retry(self):
        """HTTP 422 (Unprocessable Entity) is not retried — returns None immediately."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(status_code=422)
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)
            assert result is None
            # 4xx should NOT be retried
            assert mock_post.call_count == 1

    def test_post_called_with_correct_args(self):
        """validate_pdfua1 POSTs to /api/validate/ua1 with correct Content-Type."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_DIRECT_COMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            client.validate_pdfua1(_DUMMY_PDF)

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert "/api/validate/ua1" in call_kwargs[0][0]
            assert call_kwargs[1]["headers"]["Content-Type"] == "application/pdf"
            assert call_kwargs[1]["content"] == _DUMMY_PDF


# ---------------------------------------------------------------------------
# VeraPDFClient — parsing compliant response
# ---------------------------------------------------------------------------


class TestVeraPDFClientParseCompliantResponse:
    """Verify VeraPDFResult fields when the document is compliant."""

    def test_parse_valid_compliant_response_direct_format(self):
        """Direct-format compliant response: is_compliant True, no failed rules."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_DIRECT_COMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert isinstance(result, VeraPDFResult)
            assert result.is_compliant is True
            assert result.failed_rules == []
            assert result.failed_clauses == []
            assert result.error_count == 0
            # Two rules in the summary, both passing
            assert result.total_rules_checked == 2
            assert result.passed_rules == 2

    def test_parse_valid_compliant_response_report_format(self):
        """Nested report-format compliant response: is_compliant True."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_REPORT_COMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert result.is_compliant is True
            assert result.failed_rules == []
            assert result.error_count == 0

    def test_raw_response_preserved_on_compliant(self):
        """The raw_response field stores the original JSON payload."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_DIRECT_COMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert result.raw_response == _DIRECT_COMPLIANT


# ---------------------------------------------------------------------------
# VeraPDFClient — parsing non-compliant response
# ---------------------------------------------------------------------------


class TestVeraPDFClientParseNoncompliantResponse:
    """Verify VeraPDFResult and VeraPDFRuleFailure fields for non-compliant docs."""

    def test_parse_valid_noncompliant_response_direct_format(self):
        """Direct-format non-compliant: failed_clauses populated, error_count correct."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_DIRECT_NONCOMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert result.is_compliant is False
            # Two rules have failedChecks > 0: clauses 7.18.1 (3 failures) and 7.4 (1 failure)
            assert len(result.failed_rules) == 2
            assert result.error_count == 4  # 3 + 1
            # failed_clauses are sorted
            assert result.failed_clauses == sorted(result.failed_clauses)
            assert "7.18.1" in result.failed_clauses
            assert "7.4" in result.failed_clauses
            # Two rules passed (7.1 and 7.2)
            assert result.passed_rules == 2
            assert result.total_rules_checked == 4

    def test_failed_rule_object_fields(self):
        """VeraPDFRuleFailure objects have correct clause, description, failure_count."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_DIRECT_NONCOMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            # Find the alt-text failure
            alt_failure = next(
                (f for f in result.failed_rules if f.clause == "7.18.1"), None
            )
            assert alt_failure is not None
            assert isinstance(alt_failure, VeraPDFRuleFailure)
            assert alt_failure.failure_count == 3
            assert "Alt text" in alt_failure.description or "figures" in alt_failure.description

    def test_parse_noncompliant_report_format(self):
        """Nested report-format non-compliant: failed rule extracted from 'rules' list."""
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=_REPORT_NONCOMPLIANT
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert result.is_compliant is False
            assert len(result.failed_rules) == 1
            assert result.failed_rules[0].clause == "7.18.1"
            assert result.failed_rules[0].failure_count == 5
            assert result.error_count == 5
            assert "7.18.1" in result.failed_clauses

    def test_failed_clauses_are_sorted(self):
        """failed_clauses list is always returned in sorted order."""
        multi_failure_response: dict[str, Any] = {
            "compliant": False,
            "ruleSummaries": [
                {"ruleId": "r1", "clause": "7.18.1", "description": "D1", "failedChecks": 2},
                {"ruleId": "r2", "clause": "7.1",   "description": "D2", "failedChecks": 1},
                {"ruleId": "r3", "clause": "7.4",   "description": "D3", "failedChecks": 3},
            ],
        }
        with patch("httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                status_code=200, json_data=multi_failure_response
            )
            client = VeraPDFClient(base_url="http://localhost:8080")
            result = client.validate_pdfua1(_DUMMY_PDF)

            assert result is not None
            assert result.failed_clauses == sorted(result.failed_clauses)


# ---------------------------------------------------------------------------
# run_gate_g4_verapdf — VeraPDF unavailable
# ---------------------------------------------------------------------------


class TestGateG4VeraPDFUnavailable:
    """Gate must return passed=False with a P1 soft_fail when VeraPDF is unreachable."""

    def test_gate_unavailable_returns_soft_fail(self):
        """When VeraPDF container is down, gate fails with P1 soft_fail (fail-closed)."""
        with patch(
            "services.common.verapdf_client.VeraPDFClient.is_available",
            return_value=False,
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        assert gate_result.gate_id == "G4-VeraPDF"
        assert gate_result.passed is False  # Fail-closed — unavailability is not a pass
        assert len(gate_result.checks) == 1

        check = gate_result.checks[0]
        assert check.status == "soft_fail"
        assert check.priority == "P1"
        assert check.next_action == "flag_hitl"
        assert "not reachable" in check.details.lower() or "skipped" in check.details.lower()

    def test_gate_unavailable_check_name(self):
        """Unavailability check has expected check_name."""
        with patch(
            "services.common.verapdf_client.VeraPDFClient.is_available",
            return_value=False,
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        assert gate_result.checks[0].check_name == "verapdf_available"

    def test_gate_returns_soft_fail_when_validate_returns_none(self):
        """If is_available passes but validate_pdfua1 returns None, gate fails (fail-closed)."""
        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=None,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        assert gate_result.passed is False
        assert len(gate_result.checks) == 1
        assert gate_result.checks[0].status == "soft_fail"
        assert gate_result.checks[0].priority == "P1"


# ---------------------------------------------------------------------------
# run_gate_g4_verapdf — non-compliant result
# ---------------------------------------------------------------------------


class TestGateG4VeraPDFNoncompliant:
    """Gate must emit one P1 soft_fail per failing clause when doc is non-compliant."""

    def _noncompliant_result(self) -> VeraPDFResult:
        return VeraPDFResult(
            is_compliant=False,
            total_rules_checked=4,
            failed_rules=[
                VeraPDFRuleFailure(
                    rule_id="UA1:7.18.1-t01",
                    clause="7.18.1",
                    description="Alt text on figures",
                    test_number=1,
                    failure_count=3,
                ),
                VeraPDFRuleFailure(
                    rule_id="UA1:7.4-t01",
                    clause="7.4",
                    description="PDF/UA identifier in metadata",
                    test_number=1,
                    failure_count=1,
                ),
            ],
            passed_rules=2,
            error_count=4,
            failed_clauses=["7.18.1", "7.4"],
        )

    def test_gate_noncompliant_returns_clause_failures(self):
        """Non-compliant doc produces a P1 soft_fail check for each failing clause."""
        noncompliant = self._noncompliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=noncompliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        assert gate_result.gate_id == "G4-VeraPDF"
        # gate.passed is False when not all checks are "pass"
        assert gate_result.passed is False

        # Clause-level checks: one per failed rule
        clause_checks = [
            c for c in gate_result.checks if c.check_name.startswith("clause_")
        ]
        assert len(clause_checks) == 2  # 2 failing rules

        for check in clause_checks:
            assert check.status == "soft_fail"
            assert check.priority == "P1"
            assert check.next_action == "flag_hitl"
            assert check.severity == "serious"

    def test_gate_noncompliant_overall_compliance_check(self):
        """Non-compliant result produces a P1 soft_fail pdfua1_compliance check."""
        noncompliant = self._noncompliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=noncompliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        compliance_checks = [
            c for c in gate_result.checks if c.check_name == "pdfua1_compliance"
        ]
        assert len(compliance_checks) == 1
        check = compliance_checks[0]
        assert check.status == "soft_fail"
        assert check.priority == "P1"
        assert "non-compliant" in check.details.lower()
        assert "4" in check.details  # error_count

    def test_gate_noncompliant_clause_check_details_include_clause(self):
        """Clause checks reference the clause identifier and failure count in details."""
        noncompliant = self._noncompliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=noncompliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        clause_7_18 = next(
            (c for c in gate_result.checks if c.check_name == "clause_7.18.1"), None
        )
        assert clause_7_18 is not None
        assert "7.18.1" in clause_7_18.details
        assert "3" in clause_7_18.details  # failure_count


# ---------------------------------------------------------------------------
# run_gate_g4_verapdf — compliant result
# ---------------------------------------------------------------------------


class TestGateG4VeraPDFCompliant:
    """Gate must return all checks passing when PDF/UA-1 is fully compliant."""

    def _compliant_result(self) -> VeraPDFResult:
        return VeraPDFResult(
            is_compliant=True,
            total_rules_checked=2,
            failed_rules=[],
            passed_rules=2,
            error_count=0,
            failed_clauses=[],
        )

    def test_gate_compliant_passes(self):
        """Compliant PDF produces passed=True gate with all checks status='pass'."""
        compliant = self._compliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=compliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        assert gate_result.gate_id == "G4-VeraPDF"
        assert gate_result.passed is True
        assert all(c.status == "pass" for c in gate_result.checks)

    def test_gate_compliant_no_clause_checks(self):
        """Compliant result produces no clause_* checks (no failed rules)."""
        compliant = self._compliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=compliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        clause_checks = [
            c for c in gate_result.checks if c.check_name.startswith("clause_")
        ]
        assert clause_checks == []

    def test_gate_compliant_compliance_check_details(self):
        """pdfua1_compliance check details reference 'compliant' and zero errors."""
        compliant = self._compliant_result()

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=compliant,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)

        compliance_check = next(
            c for c in gate_result.checks if c.check_name == "pdfua1_compliance"
        )
        assert compliance_check.status == "pass"
        assert compliance_check.priority == "P2"
        assert "compliant" in compliance_check.details.lower()


# ---------------------------------------------------------------------------
# run_gate_g4_verapdf — endline vs baseline delta checks
# ---------------------------------------------------------------------------


class TestGateG4VeraPDFDelta:
    """Verify endline improvement / regression tracking when baseline is provided."""

    def _make_result(self, error_count: int, is_compliant: bool = False) -> VeraPDFResult:
        """Create a VeraPDFResult with the given error_count."""
        failed_rules = []
        if error_count > 0:
            failed_rules = [
                VeraPDFRuleFailure(
                    rule_id="UA1:7.18.1-t01",
                    clause="7.18.1",
                    description="Alt text on figures",
                    failure_count=error_count,
                )
            ]
        return VeraPDFResult(
            is_compliant=is_compliant,
            total_rules_checked=1,
            failed_rules=failed_rules,
            passed_rules=0 if error_count > 0 else 1,
            error_count=error_count,
            failed_clauses=["7.18.1"] if error_count > 0 else [],
        )

    def test_endline_improvement(self):
        """Baseline has more errors than endline: delta check passes with 'improved' detail."""
        baseline = self._make_result(error_count=10)
        endline = self._make_result(error_count=4)

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=endline,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF, baseline_result=baseline)

        delta_checks = [
            c for c in gate_result.checks if c.check_name == "verapdf_delta"
        ]
        assert len(delta_checks) == 1
        delta_check = delta_checks[0]
        assert delta_check.status == "pass"
        assert delta_check.priority == "P2"
        assert delta_check.next_action == "proceed"
        # Details mention improvement
        assert "improved" in delta_check.details.lower()
        # Counts should reference baseline -> endline
        assert "10" in delta_check.details
        assert "4" in delta_check.details

    def test_endline_equal_to_baseline_counts_as_improvement(self):
        """Equal error count (endline == baseline) is treated as 'improved' (not regression)."""
        baseline = self._make_result(error_count=5)
        endline = self._make_result(error_count=5)

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=endline,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF, baseline_result=baseline)

        delta_check = next(
            c for c in gate_result.checks if c.check_name == "verapdf_delta"
        )
        # endline_count (5) <= baseline_count (5) → improved branch
        assert delta_check.status == "pass"
        assert delta_check.next_action == "proceed"

    def test_endline_regression(self):
        """Endline has more errors than baseline: delta check soft_fails with regression detail."""
        baseline = self._make_result(error_count=2)
        endline = self._make_result(error_count=7)

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=endline,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF, baseline_result=baseline)

        delta_checks = [
            c for c in gate_result.checks if c.check_name == "verapdf_delta"
        ]
        assert len(delta_checks) == 1
        delta_check = delta_checks[0]
        assert delta_check.status == "soft_fail"
        assert delta_check.priority == "P1"
        assert delta_check.next_action == "flag_hitl"
        assert delta_check.severity == "serious"
        # Details mention regression
        assert "regressed" in delta_check.details.lower()
        # Baseline and endline counts appear in the details
        assert "2" in delta_check.details
        assert "7" in delta_check.details

    def test_no_baseline_produces_no_delta_check(self):
        """When no baseline is provided, no verapdf_delta check is generated."""
        endline = self._make_result(error_count=3)

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=endline,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF)  # No baseline_result

        delta_checks = [
            c for c in gate_result.checks if c.check_name == "verapdf_delta"
        ]
        assert delta_checks == []

    def test_gate_passed_reflects_all_checks_including_delta(self):
        """gate_result.passed is False when delta check is a soft_fail (regression)."""
        baseline = self._make_result(error_count=1)
        endline = self._make_result(error_count=8)

        with (
            patch(
                "services.common.verapdf_client.VeraPDFClient.is_available",
                return_value=True,
            ),
            patch(
                "services.common.verapdf_client.VeraPDFClient.validate_pdfua1",
                return_value=endline,
            ),
        ):
            gate_result = run_gate_g4_verapdf(_DUMMY_PDF, baseline_result=baseline)

        # gate.passed = all checks are "pass"; a soft_fail means passed is False
        assert gate_result.passed is False


# ---------------------------------------------------------------------------
# VeraPDFRuleFailure model
# ---------------------------------------------------------------------------


class TestVeraPDFRuleFailureModel:
    """Unit tests for the VeraPDFRuleFailure Pydantic model."""

    def test_defaults(self):
        """All fields have sensible defaults."""
        failure = VeraPDFRuleFailure()
        assert failure.rule_id == ""
        assert failure.clause == ""
        assert failure.description == ""
        assert failure.test_number == 0
        assert failure.failure_count == 0

    def test_explicit_values(self):
        """Explicitly set fields are stored correctly."""
        failure = VeraPDFRuleFailure(
            rule_id="UA1:7.18.1-t01",
            clause="7.18.1",
            description="Alt text missing on figures",
            test_number=1,
            failure_count=5,
        )
        assert failure.rule_id == "UA1:7.18.1-t01"
        assert failure.clause == "7.18.1"
        assert failure.failure_count == 5


# ---------------------------------------------------------------------------
# VeraPDFResult model
# ---------------------------------------------------------------------------


class TestVeraPDFResultModel:
    """Unit tests for the VeraPDFResult Pydantic model."""

    def test_defaults(self):
        """Default VeraPDFResult is non-compliant with empty lists."""
        result = VeraPDFResult()
        assert result.is_compliant is False
        assert result.total_rules_checked == 0
        assert result.failed_rules == []
        assert result.passed_rules == 0
        assert result.error_count == 0
        assert result.failed_clauses == []
        assert result.raw_response == {}

    def test_explicit_construction(self):
        """A fully populated VeraPDFResult stores all fields."""
        failure = VeraPDFRuleFailure(
            rule_id="UA1:7.1-t01",
            clause="7.1",
            description="Title missing",
            failure_count=1,
        )
        result = VeraPDFResult(
            is_compliant=False,
            total_rules_checked=3,
            failed_rules=[failure],
            passed_rules=2,
            error_count=1,
            failed_clauses=["7.1"],
            raw_response={"compliant": False},
        )
        assert result.is_compliant is False
        assert len(result.failed_rules) == 1
        assert result.failed_clauses == ["7.1"]
        assert result.raw_response == {"compliant": False}
