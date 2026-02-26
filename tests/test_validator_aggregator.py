"""Tests for multi-validator confidence scoring."""

import pytest
from services.common.validator_aggregator import (
    aggregate_validation_results,
    Confidence,
)


class TestAggregateValidationResults:
    def test_all_agree_pass_is_high_confidence(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
            verapdf_results={"1.1.1": "pass"},
            axessense_results={"1.1.1": "pass"},
        )
        assert len(results) == 1
        assert results[0].confidence == Confidence.HIGH
        assert not results[0].needs_human_review

    def test_all_agree_fail_is_high_confidence(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "fail"},
            verapdf_results={"1.1.1": "fail"},
            axessense_results={"1.1.1": "fail"},
        )
        assert results[0].confidence == Confidence.HIGH

    def test_two_of_three_agree_is_medium(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
            verapdf_results={"1.1.1": "pass"},
            axessense_results={"1.1.1": "fail"},
        )
        assert results[0].confidence == Confidence.MEDIUM
        assert not results[0].needs_human_review

    def test_even_split_is_low_with_review(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
            verapdf_results={"1.1.1": "fail"},
        )
        assert results[0].confidence == Confidence.LOW
        assert results[0].needs_human_review

    def test_single_validator_is_low(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
        )
        assert results[0].confidence == Confidence.LOW

    def test_no_results_returns_empty(self):
        results = aggregate_validation_results()
        assert results == []

    def test_skip_not_counted_as_vote(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
            verapdf_results={"1.1.1": "skip"},
            axessense_results={"1.1.1": "pass"},
        )
        assert results[0].confidence == Confidence.HIGH

    def test_multiple_rules_aggregated(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass", "1.3.1": "fail"},
            verapdf_results={"1.1.1": "pass", "1.3.1": "fail"},
        )
        assert len(results) == 2
        assert all(r.confidence == Confidence.HIGH for r in results)

    def test_validators_dict_populated(self):
        results = aggregate_validation_results(
            internal_results={"1.1.1": "pass"},
            verapdf_results={"1.1.1": "fail"},
            axessense_results={"1.1.1": "skip"},
        )
        v = results[0].validators
        assert v["internal"] == "pass"
        assert v["verapdf"] == "fail"
        assert v["axessense"] == "skip"
