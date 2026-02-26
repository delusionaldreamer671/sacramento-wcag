"""WCAG Coverage Matrix — Criterion-level and Content-type-level views.

Generates structured coverage data from the WCAG_RULES_LEDGER for
display in the frontend dashboard.
"""

from __future__ import annotations

from services.common.wcag_rules import (
    WCAG_RULES_LEDGER,
    CheckAutomation,
    PDFApplicability,
    RemediationType,
)
from services.common.wcag_techniques import (
    get_techniques_for_criterion,
    get_failures_for_criterion,
)


# ---------------------------------------------------------------------------
# WCAG Coverage Matrix (criterion-level)
# ---------------------------------------------------------------------------


def generate_coverage_matrix() -> list[dict]:
    """Return one entry per WCAG 2.1 AA criterion with automation + technique data.

    Each entry contains:
    - criterion, name, level, principle, guideline
    - pdf_applicability, automation, default_severity, default_remediation
    - pdf_techniques: list of technique refs
    - failure_techniques: list of failure refs
    - condition (for conditional applicability)
    """
    matrix = []
    for rule in WCAG_RULES_LEDGER:
        pdf_techs = get_techniques_for_criterion(rule.criterion)
        failure_techs = get_failures_for_criterion(rule.criterion)

        matrix.append({
            "criterion": rule.criterion,
            "name": rule.name,
            "level": rule.level.value,
            "principle": rule.principle.value,
            "guideline": rule.guideline,
            "description": rule.description,
            "pdf_applicability": rule.pdf_applicability.value,
            "automation": rule.automation.value,
            "default_severity": rule.default_severity.value,
            "default_remediation": rule.default_remediation.value,
            "condition": rule.condition,
            "pdf_techniques": [
                {
                    "id": t.id,
                    "title": t.title,
                    "technique_type": t.technique_type,
                    "pdf_structure": t.pdf_structure,
                    "check_description": t.check_description,
                }
                for t in pdf_techs
            ],
            "failure_techniques": [
                {
                    "id": f.id,
                    "title": f.title,
                    "description": f.description,
                    "pdf_implication": f.pdf_implication,
                }
                for f in failure_techs
            ],
        })
    return matrix


def coverage_summary() -> dict:
    """Return aggregate coverage statistics."""
    total = len(WCAG_RULES_LEDGER)
    by_level = {"A": 0, "AA": 0}
    by_automation = {"automated": 0, "semi_automated": 0, "manual": 0}
    by_applicability = {"always": 0, "conditional": 0, "never": 0}
    by_remediation = {"auto_fix": 0, "ai_draft": 0, "manual_review": 0}

    for rule in WCAG_RULES_LEDGER:
        by_level[rule.level.value] += 1
        by_automation[rule.automation.value] += 1
        by_applicability[rule.pdf_applicability.value] += 1
        by_remediation[rule.default_remediation.value] += 1

    return {
        "total_criteria": total,
        "by_level": by_level,
        "by_automation": by_automation,
        "by_applicability": by_applicability,
        "by_remediation": by_remediation,
    }


# ---------------------------------------------------------------------------
# Content-type matrix (automation vs. human by document content type)
# ---------------------------------------------------------------------------

# Map content types to relevant WCAG criteria and how they're handled
_CONTENT_TYPES: list[dict] = [
    {
        "content_type": "Images & Figures",
        "description": "Photographs, diagrams, charts, logos, icons",
        "relevant_criteria": ["1.1.1", "1.4.5", "1.4.11"],
        "automated_actions": [
            "Detect images missing alt text",
            "Flag decorative vs. informative",
            "Detect images of text",
        ],
        "ai_assisted_actions": [
            "Generate contextual alt text via Vertex AI",
            "Classify image purpose (decorative/informative)",
        ],
        "human_review_actions": [
            "Verify alt text accuracy and context",
            "Confirm decorative classification",
            "Review complex diagrams and charts",
        ],
    },
    {
        "content_type": "Tables",
        "description": "Data tables, layout tables, nested tables",
        "relevant_criteria": ["1.3.1", "1.3.2", "4.1.2"],
        "automated_actions": [
            "Detect tables missing TH/TD structure",
            "Verify RowSpan/ColSpan attributes",
            "Detect layout tables vs. data tables",
        ],
        "ai_assisted_actions": [
            "Infer header/data cell roles for ambiguous tables",
            "Generate table summaries",
        ],
        "human_review_actions": [
            "Verify complex table header associations",
            "Review nested tables (>2 levels)",
            "Confirm layout table classification",
        ],
    },
    {
        "content_type": "Headings & Structure",
        "description": "Document headings, reading order, sections",
        "relevant_criteria": ["1.3.1", "1.3.2", "2.4.1", "2.4.2", "2.4.6"],
        "automated_actions": [
            "Detect heading hierarchy violations (e.g. H1→H3 skip)",
            "Verify tag tree reading order matches visual order",
            "Check document title is set",
        ],
        "ai_assisted_actions": [
            "Classify ambiguous elements as heading vs. paragraph",
        ],
        "human_review_actions": [
            "Verify reading order for multi-column layouts",
            "Confirm heading level assignments",
        ],
    },
    {
        "content_type": "Links & Navigation",
        "description": "Hyperlinks, bookmarks, table of contents",
        "relevant_criteria": ["2.4.4", "2.4.5", "4.1.2"],
        "automated_actions": [
            "Detect links with empty or generic text ('click here')",
            "Verify link annotations have proper structure",
        ],
        "ai_assisted_actions": [
            "Suggest descriptive link text from context",
        ],
        "human_review_actions": [
            "Verify link purpose is clear in context",
            "Review navigation aids for long documents",
        ],
    },
    {
        "content_type": "Color & Contrast",
        "description": "Text color, background contrast, use of color",
        "relevant_criteria": ["1.4.1", "1.4.3", "1.4.11"],
        "automated_actions": [
            "Calculate contrast ratios for text and background",
            "Detect color-only information indicators",
        ],
        "ai_assisted_actions": [],
        "human_review_actions": [
            "Verify color is not sole means of conveying info",
            "Review contrast for embedded images of text",
        ],
    },
    {
        "content_type": "Forms & Interactive",
        "description": "Form fields, checkboxes, dropdowns, buttons",
        "relevant_criteria": ["1.3.1", "1.3.5", "2.1.1", "3.3.1", "3.3.2", "3.3.3", "3.3.4", "4.1.2"],
        "automated_actions": [
            "Detect form fields missing labels",
            "Verify keyboard tab order",
            "Check required field indicators",
        ],
        "ai_assisted_actions": [
            "Suggest labels for unlabeled form fields",
        ],
        "human_review_actions": [
            "Verify error messages are descriptive",
            "Review form field instructions",
            "Test keyboard navigation order",
        ],
    },
    {
        "content_type": "Language & Text",
        "description": "Document language, text spacing, resize",
        "relevant_criteria": ["3.1.1", "3.1.2", "1.4.4", "1.4.12"],
        "automated_actions": [
            "Detect missing /Lang attribute on document",
            "Detect missing lang tags on foreign-language passages",
            "Verify text is real text (not images of text)",
        ],
        "ai_assisted_actions": [
            "Detect language of text passages for lang tagging",
        ],
        "human_review_actions": [
            "Verify language identification for mixed-language docs",
        ],
    },
    {
        "content_type": "Multimedia",
        "description": "Embedded audio, video, animations",
        "relevant_criteria": ["1.2.1", "1.2.2", "1.2.3", "1.2.4", "1.2.5", "1.4.2", "2.3.1"],
        "automated_actions": [
            "Detect embedded multimedia objects",
        ],
        "ai_assisted_actions": [],
        "human_review_actions": [
            "Provide captions and transcripts",
            "Verify no flashing content >3 per second",
            "Provide audio descriptions where needed",
        ],
    },
]


def generate_content_type_matrix() -> list[dict]:
    """Return the content-type matrix with automation breakdown per type."""
    result = []
    for ct in _CONTENT_TYPES:
        result.append({
            "content_type": ct["content_type"],
            "description": ct["description"],
            "relevant_criteria": ct["relevant_criteria"],
            "automated_count": len(ct["automated_actions"]),
            "ai_assisted_count": len(ct["ai_assisted_actions"]),
            "human_review_count": len(ct["human_review_actions"]),
            "automated_actions": ct["automated_actions"],
            "ai_assisted_actions": ct["ai_assisted_actions"],
            "human_review_actions": ct["human_review_actions"],
        })
    return result
