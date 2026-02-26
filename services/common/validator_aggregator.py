"""Validator aggregator for multi-source validation confidence scoring.

Combines results from VeraPDF, axesSense (PAC equivalent), and the
internal 50-rule WCAG checker into per-rule confidence scores.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AggregatedRuleResult(BaseModel):
    rule_id: str
    description: str = ""
    validators: dict[str, str] = Field(default_factory=dict)  # validator_name -> "pass"|"fail"|"skip"
    confidence: Confidence = Confidence.LOW
    needs_human_review: bool = False


def aggregate_validation_results(
    internal_results: dict[str, str] | None = None,
    verapdf_results: dict[str, str] | None = None,
    axessense_results: dict[str, str] | None = None,
) -> list[AggregatedRuleResult]:
    """Aggregate results from multiple validators into confidence-scored results.

    Each input dict maps rule_id -> "pass" | "fail" | "skip".

    Confidence scoring:
    - HIGH: All active validators agree (all pass or all fail)
    - MEDIUM: 2 of 3 agree (majority wins)
    - LOW: All disagree or only 1 validator has a result
    """
    all_rule_ids: set[str] = set()
    sources = {
        "internal": internal_results or {},
        "verapdf": verapdf_results or {},
        "axessense": axessense_results or {},
    }

    for results in sources.values():
        all_rule_ids.update(results.keys())

    aggregated: list[AggregatedRuleResult] = []

    for rule_id in sorted(all_rule_ids):
        validators: dict[str, str] = {}
        votes: list[str] = []  # "pass" or "fail" only

        for name, results in sources.items():
            result = results.get(rule_id, "skip")
            validators[name] = result
            if result in ("pass", "fail"):
                votes.append(result)

        # Determine confidence
        if len(votes) == 0:
            confidence = Confidence.LOW
            needs_review = True
        elif len(votes) == 1:
            confidence = Confidence.LOW
            needs_review = False
        elif len(set(votes)) == 1:
            # All voters agree
            confidence = Confidence.HIGH
            needs_review = False
        else:
            # Disagreement
            pass_count = votes.count("pass")
            fail_count = votes.count("fail")
            if pass_count != fail_count:
                confidence = Confidence.MEDIUM
                needs_review = False
            else:
                confidence = Confidence.LOW
                needs_review = True

        aggregated.append(AggregatedRuleResult(
            rule_id=rule_id,
            validators=validators,
            confidence=confidence,
            needs_human_review=needs_review,
        ))

    return aggregated
