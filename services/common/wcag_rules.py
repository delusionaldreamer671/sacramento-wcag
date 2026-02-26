"""WCAG 2.1 AA Unified Rules Ledger.

Single source of truth for ALL WCAG 2.1 Level A + Level AA success criteria.
Source: https://www.w3.org/TR/2025/REC-WCAG21-20250506/

Every criterion is registered here. The analyzer MUST check every rule and
produce a result for each: PASS, FAIL, NOT_APPLICABLE, or ERROR.
No silent skips allowed.

Total: 50 criteria (30 Level A + 20 Level AA)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from services.common.ir import IRDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RuleLevel(str, Enum):
    A = "A"
    AA = "AA"


class Principle(str, Enum):
    PERCEIVABLE = "perceivable"
    OPERABLE = "operable"
    UNDERSTANDABLE = "understandable"
    ROBUST = "robust"


class PDFApplicability(str, Enum):
    """Whether a WCAG criterion applies to static PDF documents."""
    ALWAYS = "always"              # Applies to every PDF
    CONDITIONAL = "conditional"    # Applies only if PDF has certain content (forms, media, etc.)
    NEVER = "never"                # Does not apply to static PDFs


class CheckAutomation(str, Enum):
    """How the check can be performed."""
    AUTOMATED = "automated"            # Fully automated programmatic check
    SEMI_AUTOMATED = "semi_automated"  # Automated detection + human verification needed
    MANUAL = "manual"                  # Requires human review only


class FindingStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"


class RemediationType(str, Enum):
    AUTO_FIX = "auto_fix"
    AI_DRAFT = "ai_draft"
    MANUAL_REVIEW = "manual_review"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RuleFinding:
    """A single finding from a rule check."""
    rule_id: str
    criterion: str
    rule_name: str
    status: FindingStatus
    severity: FindingSeverity
    element_id: str = ""
    page: int = 0
    description: str = ""
    proposed_fix: str = ""
    evidence: str = ""                       # Why it failed — deterministic proof
    remediation_type: RemediationType = RemediationType.AUTO_FIX
    auto_fixable: bool = False                   # Must be set explicitly; derived from remediation_type in _fail()


@dataclass
class WCAGRule:
    """A single WCAG 2.1 success criterion entry in the ledger."""
    rule_id: str                             # e.g. "wcag_1_1_1"
    criterion: str                           # e.g. "1.1.1"
    name: str                                # e.g. "Non-text Content"
    level: RuleLevel                         # A or AA
    principle: Principle
    guideline: str                           # e.g. "1.1 Text Alternatives"
    description: str                         # Official requirement text
    pdf_applicability: PDFApplicability
    pdf_check_description: str               # What to check in a PDF
    automation: CheckAutomation
    default_severity: FindingSeverity
    default_remediation: RemediationType
    condition: str = ""                      # When CONDITIONAL, what triggers applicability
    check_fn_name: str = ""                  # Name of the check function (for traceability)
    pdf_techniques: list[str] = field(default_factory=list)      # e.g. ["PDF1", "PDF4"]
    failure_techniques: list[str] = field(default_factory=list)   # e.g. ["F30", "F65"]


@dataclass
class AuditResult:
    """Complete audit result from running all rules."""
    findings: list[RuleFinding] = field(default_factory=list)
    rules_checked: int = 0
    rules_passed: int = 0
    rules_failed: int = 0
    rules_not_applicable: int = 0
    rules_errored: int = 0
    coverage_pct: float = 0.0                # % of applicable rules that produced a result


# ---------------------------------------------------------------------------
# THE LEDGER — All 50 WCAG 2.1 Level A + Level AA Success Criteria
# ---------------------------------------------------------------------------
#
# Source: https://www.w3.org/TR/2025/REC-WCAG21-20250506/
#
# Organized by: Principle → Guideline → Criterion
# Every criterion is here. None are omitted.
#
# ---------------------------------------------------------------------------

WCAG_RULES_LEDGER: list[WCAGRule] = [

    # ===================================================================
    # PRINCIPLE 1: PERCEIVABLE
    # ===================================================================

    # --- Guideline 1.1: Text Alternatives ---

    WCAGRule(
        rule_id="wcag_1_1_1",
        criterion="1.1.1",
        name="Non-text Content",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.1 Text Alternatives",
        description=(
            "All non-text content that is presented to the user has a text "
            "alternative that serves the equivalent purpose, except for controls, "
            "time-based media, tests, sensory, CAPTCHA, and decoration."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Every <Figure> tag in the PDF tag tree must have an Alt attribute "
            "with meaningful descriptive text. Decorative images must be marked "
            "as Artifact. Check: (1) all IMAGE blocks have non-empty, "
            "non-placeholder alt text, (2) decorative images are tagged as "
            "Artifact, not Figure."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.AI_DRAFT,
        check_fn_name="check_1_1_1_non_text_content",
        pdf_techniques=["PDF1", "PDF4"],
        failure_techniques=["F30", "F38", "F39", "F65"],
    ),

    # --- Guideline 1.2: Time-based Media ---

    WCAGRule(
        rule_id="wcag_1_2_1",
        criterion="1.2.1",
        name="Audio-only and Video-only (Prerecorded)",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.2 Time-based Media",
        description=(
            "For prerecorded audio-only and prerecorded video-only media, "
            "an alternative is provided."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded audio or video objects, each must "
            "have a text transcript or alternative. Check: scan for embedded "
            "multimedia annotations."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains embedded audio or video",
        check_fn_name="check_1_2_1_audio_video_only",
    ),

    WCAGRule(
        rule_id="wcag_1_2_2",
        criterion="1.2.2",
        name="Captions (Prerecorded)",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.2 Time-based Media",
        description=(
            "Captions are provided for all prerecorded audio content in "
            "synchronized media."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded synchronized media (video with audio), "
            "captions must be provided. Check: scan for multimedia annotations."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains synchronized media",
        check_fn_name="check_1_2_2_captions_prerecorded",
    ),

    WCAGRule(
        rule_id="wcag_1_2_3",
        criterion="1.2.3",
        name="Audio Description or Media Alternative (Prerecorded)",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.2 Time-based Media",
        description=(
            "An alternative for time-based media or audio description of the "
            "prerecorded video content is provided for synchronized media."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded video, an audio description or text "
            "alternative must be provided. Check: scan for video annotations."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains synchronized media",
        check_fn_name="check_1_2_3_audio_description",
    ),

    WCAGRule(
        rule_id="wcag_1_2_4",
        criterion="1.2.4",
        name="Captions (Live)",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.2 Time-based Media",
        description=(
            "Captions are provided for all live audio content in synchronized media."
        ),
        pdf_applicability=PDFApplicability.NEVER,
        pdf_check_description="Not applicable to static PDF documents — no live media.",
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_2_4_captions_live",
    ),

    WCAGRule(
        rule_id="wcag_1_2_5",
        criterion="1.2.5",
        name="Audio Description (Prerecorded)",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.2 Time-based Media",
        description=(
            "Audio description is provided for all prerecorded video content "
            "in synchronized media."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded prerecorded video, audio description "
            "must be provided. Check: scan for video annotations."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains prerecorded video",
        check_fn_name="check_1_2_5_audio_description_prerecorded",
    ),

    # --- Guideline 1.3: Adaptable ---

    WCAGRule(
        rule_id="wcag_1_3_1",
        criterion="1.3.1",
        name="Info and Relationships",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.3 Adaptable",
        description=(
            "Information, structure, and relationships conveyed through "
            "presentation can be programmatically determined or are available "
            "in text."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) headings use proper H1-H6 tags, not just bold/large text, "
            "(2) tables use Table/TR/TH/TD tags with header associations, "
            "(3) lists use L/LI/Lbl/LBody tags, (4) form fields have labels, "
            "(5) visual groupings are reflected in tag structure."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_3_1_info_relationships",
        pdf_techniques=["PDF6", "PDF9", "PDF10", "PDF11", "PDF12", "PDF17", "PDF20", "PDF21"],
        failure_techniques=["F43", "F46", "F90", "F91"],
    ),

    WCAGRule(
        rule_id="wcag_1_3_2",
        criterion="1.3.2",
        name="Meaningful Sequence",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.3 Adaptable",
        description=(
            "When the sequence in which content is presented affects its meaning, "
            "a correct reading sequence can be programmatically determined."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) tag tree order matches intended reading order, "
            "(2) multi-column layouts have correct reading sequence in tags, "
            "(3) floating elements (sidebars, captions) are in logical position "
            "within the tag tree."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_3_2_meaningful_sequence",
        pdf_techniques=["PDF3"],
    ),

    WCAGRule(
        rule_id="wcag_1_3_3",
        criterion="1.3.3",
        name="Sensory Characteristics",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.3 Adaptable",
        description=(
            "Instructions provided for understanding and operating content do "
            "not rely solely on sensory characteristics of components such as "
            "shape, color, size, visual location, orientation, or sound."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: scan text content for phrases like 'click the red button', "
            "'see the chart on the left', 'the round icon' that rely solely on "
            "sensory characteristics. Flag for human review."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_3_3_sensory_characteristics",
    ),

    WCAGRule(
        rule_id="wcag_1_3_4",
        criterion="1.3.4",
        name="Orientation",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.3 Adaptable",
        description=(
            "Content does not restrict its view and operation to a single "
            "display orientation, such as portrait or landscape, unless a "
            "specific display orientation is essential."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF does not have ViewerPreferences that lock rotation, "
            "(2) content is usable in both portrait and landscape. For tagged "
            "PDFs this is generally satisfied. Flag if mixed page orientations "
            "exist without proper reflow support."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_3_4_orientation",
    ),

    WCAGRule(
        rule_id="wcag_1_3_5",
        criterion="1.3.5",
        name="Identify Input Purpose",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.3 Adaptable",
        description=(
            "The purpose of each input field collecting information about the "
            "user can be programmatically determined when the field serves a "
            "purpose identified in the Input Purposes for User Interface "
            "Components section."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields collecting personal data (name, "
            "email, address, etc.), each field must have autocomplete-equivalent "
            "attributes identifying its purpose. Check: scan AcroForm fields "
            "for TU (tooltip) descriptions matching known input purposes."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains form fields collecting user information",
        check_fn_name="check_1_3_5_identify_input_purpose",
    ),

    # --- Guideline 1.4: Distinguishable ---

    WCAGRule(
        rule_id="wcag_1_4_1",
        criterion="1.4.1",
        name="Use of Color",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "Color is not used as the only visual means of conveying information, "
            "indicating an action, prompting a response, or distinguishing a "
            "visual element."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) links are distinguishable by more than color alone "
            "(underline, bold, or other visual cue), (2) charts and graphs "
            "don't rely solely on color to convey data — look for patterns, "
            "labels, or legends. Semi-automated: detect colored text without "
            "additional differentiation."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_4_1_use_of_color",
    ),

    WCAGRule(
        rule_id="wcag_1_4_2",
        criterion="1.4.2",
        name="Audio Control",
        level=RuleLevel.A,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "If any audio on a web page plays automatically for more than 3 "
            "seconds, either a mechanism is available to pause or stop the audio, "
            "or a mechanism is available to control audio volume independently "
            "from the overall system volume level."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded audio that auto-plays, there must be "
            "a control to pause/stop it. Check: scan for Sound actions or "
            "RichMedia annotations with autoplay."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains auto-playing audio",
        check_fn_name="check_1_4_2_audio_control",
    ),

    WCAGRule(
        rule_id="wcag_1_4_3",
        criterion="1.4.3",
        name="Contrast (Minimum)",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "The visual presentation of text and images of text has a contrast "
            "ratio of at least 4.5:1, except for large text (3:1), incidental "
            "text, and logotypes."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) extract text color and background color from content "
            "streams, (2) compute luminance contrast ratio per WCAG formula, "
            "(3) normal text (<18pt or <14pt bold) must be >= 4.5:1, "
            "(4) large text (>=18pt or >=14pt bold) must be >= 3:1. "
            "Flag any text blocks below threshold."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_4_3_contrast_minimum",
    ),

    WCAGRule(
        rule_id="wcag_1_4_4",
        criterion="1.4.4",
        name="Resize Text",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "Except for captions and images of text, text can be resized without "
            "assistive technology up to 200 percent without loss of content or "
            "functionality."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF is tagged (tagged PDFs support reflow/zoom), "
            "(2) text is not embedded as images (would not resize), "
            "(3) content remains accessible when zoomed to 200%. "
            "A properly tagged PDF generally satisfies this criterion."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_4_4_resize_text",
    ),

    WCAGRule(
        rule_id="wcag_1_4_5",
        criterion="1.4.5",
        name="Images of Text",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "If the technologies being used can achieve the visual presentation, "
            "text is used to convey information rather than images of text, "
            "except when customizable or essential."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) scan for IMAGE blocks that contain text (OCR or "
            "known patterns like scanned text), (2) check if Figure tags "
            "contain what should be real text content, (3) flag images that "
            "appear to be screenshots of text or scanned documents."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_4_5_images_of_text",
        pdf_techniques=["PDF7"],
    ),

    WCAGRule(
        rule_id="wcag_1_4_10",
        criterion="1.4.10",
        name="Reflow",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "Content can be presented without loss of information or "
            "functionality, and without requiring scrolling in two dimensions "
            "for: vertical scrolling content at a width equivalent to 320 CSS "
            "pixels; horizontal scrolling content at a height equivalent to "
            "256 CSS pixels. Except for content which requires two-dimensional "
            "layout for usage or meaning."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF is tagged (enables reflow mode in readers), "
            "(2) content structure supports linearization, (3) tables, images, "
            "and multi-column layouts have proper tag structure for reflow. "
            "A properly tagged PDF satisfies this. Untagged PDFs fail."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_4_10_reflow",
    ),

    WCAGRule(
        rule_id="wcag_1_4_11",
        criterion="1.4.11",
        name="Non-text Contrast",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "The visual presentation of user interface components and graphical "
            "objects have a contrast ratio of at least 3:1 against adjacent "
            "color(s), except when inactive or appearance determined by user agent."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) form field borders and icons have 3:1 contrast, "
            "(2) chart/graph elements (bars, lines, pie segments) have 3:1 "
            "contrast against their background, (3) icons and meaningful "
            "graphical elements meet 3:1 contrast."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_1_4_11_non_text_contrast",
    ),

    WCAGRule(
        rule_id="wcag_1_4_12",
        criterion="1.4.12",
        name="Text Spacing",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "No loss of content or functionality occurs by setting: line height "
            "to at least 1.5 times the font size; spacing following paragraphs "
            "to at least 2 times the font size; letter spacing to at least "
            "0.12 times the font size; word spacing to at least 0.16 times "
            "the font size."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF is tagged (tagged PDFs with proper structure "
            "support text spacing adjustments in assistive technology), "
            "(2) text is not rendered as fixed-position images. "
            "A properly tagged PDF generally satisfies this criterion. "
            "Note: PDF viewers handle spacing via reflow mode."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_1_4_12_text_spacing",
    ),

    WCAGRule(
        rule_id="wcag_1_4_13",
        criterion="1.4.13",
        name="Content on Hover or Focus",
        level=RuleLevel.AA,
        principle=Principle.PERCEIVABLE,
        guideline="1.4 Distinguishable",
        description=(
            "Where receiving and then removing pointer hover or keyboard focus "
            "triggers additional content to become visible and then hidden, "
            "the additional content is dismissible, hoverable, and persistent."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains JavaScript actions that show/hide content on "
            "hover or focus (tooltips, popups), they must be dismissible and "
            "persistent. Check: scan for MouseEnter/MouseExit actions. "
            "Most static PDFs have no such content."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains hover/focus-triggered content",
        check_fn_name="check_1_4_13_content_on_hover_focus",
    ),

    # ===================================================================
    # PRINCIPLE 2: OPERABLE
    # ===================================================================

    # --- Guideline 2.1: Keyboard Accessible ---

    WCAGRule(
        rule_id="wcag_2_1_1",
        criterion="2.1.1",
        name="Keyboard",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.1 Keyboard Accessible",
        description=(
            "All functionality of the content is operable through a keyboard "
            "interface without requiring specific timings for individual "
            "keystrokes, except where the underlying function requires input "
            "that depends on the path of the user's movement."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) all interactive elements (links, form fields, buttons) "
            "are in the tab order, (2) document has a defined tab order "
            "(StructParents or /Tabs /S), (3) all link annotations are "
            "keyboard-reachable. A properly tagged PDF with tab order "
            "set to Structure satisfies this."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_1_1_keyboard",
        pdf_techniques=["PDF3", "PDF11", "PDF23"],
    ),

    WCAGRule(
        rule_id="wcag_2_1_2",
        criterion="2.1.2",
        name="No Keyboard Trap",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.1 Keyboard Accessible",
        description=(
            "If keyboard focus can be moved to a component using a keyboard "
            "interface, then focus can be moved away from that component using "
            "only a keyboard interface."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains interactive form fields or embedded widgets, "
            "verify that keyboard focus can exit each element. Standard PDF "
            "form fields do not create keyboard traps. Check: scan for "
            "JavaScript actions that might trap focus."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains interactive elements or JavaScript",
        check_fn_name="check_2_1_2_no_keyboard_trap",
    ),

    WCAGRule(
        rule_id="wcag_2_1_4",
        criterion="2.1.4",
        name="Character Key Shortcuts",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.1 Keyboard Accessible",
        description=(
            "If a keyboard shortcut is implemented in content using only letter, "
            "punctuation, number, or symbol characters, then a mechanism is "
            "available to turn the shortcut off, remap it, or make it active "
            "only on focus."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains JavaScript that implements keyboard shortcuts "
            "using single character keys, a mechanism to remap or disable them "
            "must exist. Check: scan for Keystroke actions. Most static PDFs "
            "have no custom keyboard shortcuts."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains custom keyboard shortcuts",
        check_fn_name="check_2_1_4_character_key_shortcuts",
    ),

    # --- Guideline 2.2: Enough Time ---

    WCAGRule(
        rule_id="wcag_2_2_1",
        criterion="2.2.1",
        name="Timing Adjustable",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.2 Enough Time",
        description=(
            "For each time limit set by content, at least one option is "
            "available: turn off, adjust, or extend the time limit."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains timed interactions (JavaScript timers, "
            "auto-submit forms), time limits must be adjustable. Check: scan "
            "for timer-related JavaScript actions. Most static PDFs have "
            "no time limits."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains time-limited interactions",
        check_fn_name="check_2_2_1_timing_adjustable",
    ),

    WCAGRule(
        rule_id="wcag_2_2_2",
        criterion="2.2.2",
        name="Pause, Stop, Hide",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.2 Enough Time",
        description=(
            "For moving, blinking, scrolling, or auto-updating information, "
            "a mechanism is available for the user to pause, stop, or hide it."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains animated content (animated GIFs, embedded "
            "Flash, auto-scrolling), a pause/stop mechanism must exist. Check: "
            "scan for animated annotations or RichMedia. Most static PDFs "
            "have no animation."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains moving, blinking, or auto-updating content",
        check_fn_name="check_2_2_2_pause_stop_hide",
    ),

    # --- Guideline 2.3: Seizures and Physical Reactions ---

    WCAGRule(
        rule_id="wcag_2_3_1",
        criterion="2.3.1",
        name="Three Flashes or Below Threshold",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.3 Seizures and Physical Reactions",
        description=(
            "Web pages do not contain anything that flashes more than three "
            "times in any one second period, or the flash is below the general "
            "flash and red flash thresholds."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains embedded animations or video, they must not "
            "flash more than 3 times per second. Check: scan for multimedia "
            "annotations. Most static PDFs have no flashing content."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains animated or video content",
        check_fn_name="check_2_3_1_three_flashes",
    ),

    # --- Guideline 2.4: Navigable ---

    WCAGRule(
        rule_id="wcag_2_4_1",
        criterion="2.4.1",
        name="Bypass Blocks",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description=(
            "A mechanism is available to bypass blocks of content that are "
            "repeated on multiple web pages."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF has bookmarks/outlines for navigation, "
            "(2) headings are properly tagged so assistive technology can "
            "skip between sections, (3) for multi-page documents, a table of "
            "contents or bookmark tree exists."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_1_bypass_blocks",
        pdf_techniques=["PDF9"],
    ),

    WCAGRule(
        rule_id="wcag_2_4_2",
        criterion="2.4.2",
        name="Page Titled",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description="Web pages have titles that describe topic or purpose.",
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF metadata contains a non-empty Title field "
            "(dc:title in XMP or /Title in Info dict), (2) ViewerPreferences "
            "has DisplayDocTitle = true so the title is shown instead of "
            "the filename."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_2_page_titled",
        pdf_techniques=["PDF18"],
        failure_techniques=["F25"],
    ),

    WCAGRule(
        rule_id="wcag_2_4_3",
        criterion="2.4.3",
        name="Focus Order",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description=(
            "If a web page can be navigated sequentially and the navigation "
            "sequences affect meaning or operation, focusable components "
            "receive focus in an order that preserves meaning and operability."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) tab order is set to 'Structure' (/Tabs /S) on each "
            "page so focus follows the tag tree order, (2) the tag tree order "
            "matches the logical reading order, (3) links and form fields "
            "are encountered in a meaningful sequence."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_3_focus_order",
        pdf_techniques=["PDF3"],
    ),

    WCAGRule(
        rule_id="wcag_2_4_4",
        criterion="2.4.4",
        name="Link Purpose (In Context)",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description=(
            "The purpose of each link can be determined from the link text "
            "alone or from the link text together with its programmatically "
            "determined link context."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) all link annotations have descriptive text — not "
            "'click here', 'here', 'read more', or raw URLs, (2) link text "
            "describes the destination, (3) links that are just URLs have "
            "a descriptive alt text or surrounding context."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AI_DRAFT,
        check_fn_name="check_2_4_4_link_purpose",
        pdf_techniques=["PDF11", "PDF13"],
    ),

    WCAGRule(
        rule_id="wcag_2_4_5",
        criterion="2.4.5",
        name="Multiple Ways",
        level=RuleLevel.AA,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description=(
            "More than one way is available to locate a web page within a "
            "set of web pages except where the web page is the result of, "
            "or a step in, a process."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF has bookmarks/outlines AND tagged headings "
            "(two ways to navigate), (2) for longer documents, a table of "
            "contents exists in addition to bookmarks, (3) page labels are "
            "set for page-number navigation."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_5_multiple_ways",
        pdf_techniques=["PDF2"],
    ),

    WCAGRule(
        rule_id="wcag_2_4_6",
        criterion="2.4.6",
        name="Headings and Labels",
        level=RuleLevel.AA,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description="Headings and labels describe topic or purpose.",
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) heading tags (H1-H6) have meaningful text content — "
            "not empty or generic, (2) heading hierarchy is logical — no "
            "skipped levels (H1 → H3), (3) form field labels describe the "
            "expected input, (4) headings accurately describe their sections."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_6_headings_and_labels",
    ),

    WCAGRule(
        rule_id="wcag_2_4_7",
        criterion="2.4.7",
        name="Focus Visible",
        level=RuleLevel.AA,
        principle=Principle.OPERABLE,
        guideline="2.4 Navigable",
        description=(
            "Any keyboard operable user interface has a mode of operation "
            "where the keyboard focus indicator is visible."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: this is primarily a PDF viewer responsibility — Acrobat "
            "and other readers provide visible focus indicators. The PDF must "
            "have proper tab order (/Tabs /S) so the viewer can show focus. "
            "Verify tab order is set."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_2_4_7_focus_visible",
    ),

    # --- Guideline 2.5: Input Modalities ---

    WCAGRule(
        rule_id="wcag_2_5_1",
        criterion="2.5.1",
        name="Pointer Gestures",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.5 Input Modalities",
        description=(
            "All functionality that uses multipoint or path-based gestures "
            "for operation can be operated with a single pointer without a "
            "path-based gesture, unless a multipoint or path-based gesture "
            "is essential."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains interactive elements requiring multipoint "
            "gestures (pinch, multi-finger swipe), single-pointer alternatives "
            "must exist. Standard PDF viewing does not require multipoint "
            "gestures. Check: scan for JavaScript gesture handlers."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains multipoint gesture interactions",
        check_fn_name="check_2_5_1_pointer_gestures",
    ),

    WCAGRule(
        rule_id="wcag_2_5_2",
        criterion="2.5.2",
        name="Pointer Cancellation",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.5 Input Modalities",
        description=(
            "For functionality that can be operated using a single pointer, "
            "at least one of the following is true: no down-event, abort/undo, "
            "up reversal, or essential."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF has JavaScript-driven interactions triggered by "
            "mouse-down events, they must be cancellable. Standard PDF links "
            "and form fields activate on click (up-event). Check: scan for "
            "MouseDown actions."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains pointer-down triggered actions",
        check_fn_name="check_2_5_2_pointer_cancellation",
    ),

    WCAGRule(
        rule_id="wcag_2_5_3",
        criterion="2.5.3",
        name="Label in Name",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.5 Input Modalities",
        description=(
            "For user interface components with labels that include text or "
            "images of text, the name contains the text that is presented "
            "visually."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields or buttons with visible labels, "
            "the accessible name (TU tooltip or /T field name) must contain "
            "the visible label text. Check: compare visible button/field text "
            "with the accessible name."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains labeled UI components (form fields, buttons)",
        check_fn_name="check_2_5_3_label_in_name",
    ),

    WCAGRule(
        rule_id="wcag_2_5_4",
        criterion="2.5.4",
        name="Motion Actuation",
        level=RuleLevel.A,
        principle=Principle.OPERABLE,
        guideline="2.5 Input Modalities",
        description=(
            "Functionality that can be operated by device motion or user "
            "motion can also be operated by user interface components and "
            "responding to the motion can be disabled to prevent accidental "
            "actuation."
        ),
        pdf_applicability=PDFApplicability.NEVER,
        pdf_check_description=(
            "Not applicable to static PDF documents — PDFs do not use device "
            "motion for functionality."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_2_5_4_motion_actuation",
    ),

    # ===================================================================
    # PRINCIPLE 3: UNDERSTANDABLE
    # ===================================================================

    # --- Guideline 3.1: Readable ---

    WCAGRule(
        rule_id="wcag_3_1_1",
        criterion="3.1.1",
        name="Language of Page",
        level=RuleLevel.A,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.1 Readable",
        description=(
            "The default human language of each web page can be "
            "programmatically determined."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF catalog has a /Lang entry with a valid BCP 47 "
            "language tag (e.g. 'en', 'en-US', 'es'), (2) the language tag "
            "is not empty or invalid."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_3_1_1_language_of_page",
        pdf_techniques=["PDF16", "PDF19"],
    ),

    WCAGRule(
        rule_id="wcag_3_1_2",
        criterion="3.1.2",
        name="Language of Parts",
        level=RuleLevel.AA,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.1 Readable",
        description=(
            "The human language of each passage or phrase in the content can "
            "be programmatically determined except for proper names, technical "
            "terms, words of indeterminate language, and words that have become "
            "part of the vernacular."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) if the document contains passages in a different "
            "language than the document default, those passages must have "
            "a /Lang attribute on their tag, (2) scan text blocks for likely "
            "foreign-language content and verify /Lang tagging."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        check_fn_name="check_3_1_2_language_of_parts",
        pdf_techniques=["PDF19"],
    ),

    # --- Guideline 3.2: Predictable ---

    WCAGRule(
        rule_id="wcag_3_2_1",
        criterion="3.2.1",
        name="On Focus",
        level=RuleLevel.A,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.2 Predictable",
        description=(
            "When any user interface component receives focus, it does not "
            "initiate a change of context."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields or JavaScript, receiving focus "
            "must not trigger context changes (page navigation, dialog). "
            "Check: scan for OnFocus/GotFocus JavaScript actions."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains interactive elements with focus handlers",
        check_fn_name="check_3_2_1_on_focus",
    ),

    WCAGRule(
        rule_id="wcag_3_2_2",
        criterion="3.2.2",
        name="On Input",
        level=RuleLevel.A,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.2 Predictable",
        description=(
            "Changing the setting of any user interface component does not "
            "automatically cause a change of context unless the user has "
            "been advised of the behavior before using the component."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields, changing a field value must "
            "not trigger unexpected navigation or submission. Check: scan "
            "for onChange/Validate JavaScript actions on form fields."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains form fields with change handlers",
        check_fn_name="check_3_2_2_on_input",
        pdf_techniques=["PDF15"],
    ),

    WCAGRule(
        rule_id="wcag_3_2_3",
        criterion="3.2.3",
        name="Consistent Navigation",
        level=RuleLevel.AA,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.2 Predictable",
        description=(
            "Navigational mechanisms that are repeated on multiple web pages "
            "within a set of web pages occur in the same relative order each "
            "time they are repeated, unless a change is initiated by the user."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "For PDF document sets (e.g. a series of reports), repeated "
            "navigation elements (headers, footers, TOC links) should appear "
            "in the same order. For single documents, check that repeated "
            "page elements (headers, footers) are consistent. Usually N/A "
            "for standalone documents."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.MINOR,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF is part of a document set with repeated navigation",
        check_fn_name="check_3_2_3_consistent_navigation",
        pdf_techniques=["PDF14", "PDF17"],
    ),

    WCAGRule(
        rule_id="wcag_3_2_4",
        criterion="3.2.4",
        name="Consistent Identification",
        level=RuleLevel.AA,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.2 Predictable",
        description=(
            "Components that have the same functionality within a set of "
            "web pages are identified consistently."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "For PDF document sets, components with the same functionality "
            "(e.g. 'Submit' buttons, navigation links) should use the same "
            "labels. For single documents, check that repeated elements "
            "use consistent naming. Usually N/A for standalone documents."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.MINOR,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF is part of a document set",
        check_fn_name="check_3_2_4_consistent_identification",
    ),

    # --- Guideline 3.3: Input Assistance ---

    WCAGRule(
        rule_id="wcag_3_3_1",
        criterion="3.3.1",
        name="Error Identification",
        level=RuleLevel.A,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.3 Input Assistance",
        description=(
            "If an input error is automatically detected, the item that is "
            "in error is identified and the error is described to the user "
            "in text."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields with validation, errors must "
            "be described in text. Check: (1) form fields with format/validate "
            "actions have error messages, (2) required fields are identified, "
            "(3) validation scripts provide textual error descriptions."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains form fields with validation",
        check_fn_name="check_3_3_1_error_identification",
        pdf_techniques=["PDF5", "PDF22"],
    ),

    WCAGRule(
        rule_id="wcag_3_3_2",
        criterion="3.3.2",
        name="Labels or Instructions",
        level=RuleLevel.A,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.3 Input Assistance",
        description=(
            "Labels or instructions are provided when content requires "
            "user input."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields, each field must have a visible "
            "label or instructions. Check: (1) every form field has a /TU "
            "tooltip or associated label, (2) required fields are marked, "
            "(3) instructions are provided for complex input formats."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        condition="PDF contains form fields",
        check_fn_name="check_3_3_2_labels_or_instructions",
        pdf_techniques=["PDF5", "PDF10"],
    ),

    WCAGRule(
        rule_id="wcag_3_3_3",
        criterion="3.3.3",
        name="Error Suggestion",
        level=RuleLevel.AA,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.3 Input Assistance",
        description=(
            "If an input error is automatically detected and suggestions for "
            "correction are known, then the suggestions are provided to the "
            "user, unless it would jeopardize the security or purpose of "
            "the content."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields with validation and the correct "
            "format is known, error messages must include suggestions. Check: "
            "validation scripts provide corrective suggestions, not just "
            "'invalid input'."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains form fields with validation",
        check_fn_name="check_3_3_3_error_suggestion",
        pdf_techniques=["PDF22"],
    ),

    WCAGRule(
        rule_id="wcag_3_3_4",
        criterion="3.3.4",
        name="Error Prevention (Legal, Financial, Data)",
        level=RuleLevel.AA,
        principle=Principle.UNDERSTANDABLE,
        guideline="3.3 Input Assistance",
        description=(
            "For web pages that cause legal commitments or financial "
            "transactions for the user, that modify or delete user-controllable "
            "data, or that submit user test responses: submissions are "
            "reversible, data is checked for errors, or a confirmation "
            "mechanism is provided."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains forms that submit legal/financial data, "
            "a review/confirmation step must be provided before submission. "
            "Check: submit actions have confirmation dialogs or review pages."
        ),
        automation=CheckAutomation.MANUAL,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains forms with legal/financial submissions",
        check_fn_name="check_3_3_4_error_prevention",
    ),

    # ===================================================================
    # PRINCIPLE 4: ROBUST
    # ===================================================================

    # --- Guideline 4.1: Compatible ---

    WCAGRule(
        rule_id="wcag_4_1_1",
        criterion="4.1.1",
        name="Parsing",
        level=RuleLevel.A,
        principle=Principle.ROBUST,
        guideline="4.1 Compatible",
        description=(
            "In content implemented using markup languages, elements have "
            "complete start and end tags, elements are nested according to "
            "their specifications, elements do not contain duplicate attributes, "
            "and any IDs are unique."
        ),
        pdf_applicability=PDFApplicability.ALWAYS,
        pdf_check_description=(
            "Check: (1) PDF tag tree is well-formed — no broken nesting, "
            "no orphaned tags, (2) structure element IDs are unique, "
            "(3) role mappings are valid, (4) content items are correctly "
            "associated with structure elements."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.SERIOUS,
        default_remediation=RemediationType.AUTO_FIX,
        check_fn_name="check_4_1_1_parsing",
    ),

    WCAGRule(
        rule_id="wcag_4_1_2",
        criterion="4.1.2",
        name="Name, Role, Value",
        level=RuleLevel.A,
        principle=Principle.ROBUST,
        guideline="4.1 Compatible",
        description=(
            "For all user interface components, the name and role can be "
            "programmatically determined; states, properties, and values that "
            "can be set by the user can be programmatically set; and "
            "notification of changes to these items is available to user agents, "
            "including assistive technologies."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains form fields, links, or buttons: (1) each "
            "has a programmatically determinable name (/T, /TU, or /Alt), "
            "(2) role is defined by the field type or tag, (3) states are "
            "exposed (checked, selected, etc.). Check form field attributes."
        ),
        automation=CheckAutomation.AUTOMATED,
        default_severity=FindingSeverity.CRITICAL,
        default_remediation=RemediationType.AUTO_FIX,
        condition="PDF contains form fields, links, or buttons",
        check_fn_name="check_4_1_2_name_role_value",
        pdf_techniques=["PDF10", "PDF12"],
        failure_techniques=["F68", "F86"],
    ),

    WCAGRule(
        rule_id="wcag_4_1_3",
        criterion="4.1.3",
        name="Status Messages",
        level=RuleLevel.AA,
        principle=Principle.ROBUST,
        guideline="4.1 Compatible",
        description=(
            "In content implemented using markup languages, status messages "
            "can be programmatically determined through role or properties "
            "such that they can be presented to the user by assistive "
            "technologies without receiving focus."
        ),
        pdf_applicability=PDFApplicability.CONDITIONAL,
        pdf_check_description=(
            "If the PDF contains dynamic content that generates status "
            "messages (JavaScript alerts, form validation messages), these "
            "must be accessible without requiring focus. Check: scan for "
            "alert/status JavaScript actions."
        ),
        automation=CheckAutomation.SEMI_AUTOMATED,
        default_severity=FindingSeverity.MODERATE,
        default_remediation=RemediationType.MANUAL_REVIEW,
        condition="PDF contains dynamic status messages",
        check_fn_name="check_4_1_3_status_messages",
    ),
]


# ---------------------------------------------------------------------------
# Ledger integrity assertions
# ---------------------------------------------------------------------------


def _validate_ledger() -> None:
    """Verify the ledger is complete and consistent. Run at import time."""
    # 1. Check total count
    assert len(WCAG_RULES_LEDGER) == 50, (
        f"WCAG 2.1 AA requires exactly 50 criteria (30 A + 20 AA). "
        f"Ledger has {len(WCAG_RULES_LEDGER)}."
    )

    # 2. Check Level A count
    level_a = [r for r in WCAG_RULES_LEDGER if r.level == RuleLevel.A]
    assert len(level_a) == 30, (
        f"Expected 30 Level A criteria, found {len(level_a)}."
    )

    # 3. Check Level AA count
    level_aa = [r for r in WCAG_RULES_LEDGER if r.level == RuleLevel.AA]
    assert len(level_aa) == 20, (
        f"Expected 20 Level AA criteria, found {len(level_aa)}."
    )

    # 4. Check unique rule_ids
    ids = [r.rule_id for r in WCAG_RULES_LEDGER]
    assert len(ids) == len(set(ids)), (
        f"Duplicate rule_ids found: "
        f"{[x for x in ids if ids.count(x) > 1]}"
    )

    # 5. Check unique criteria
    criteria = [r.criterion for r in WCAG_RULES_LEDGER]
    assert len(criteria) == len(set(criteria)), (
        f"Duplicate criteria found: "
        f"{[x for x in criteria if criteria.count(x) > 1]}"
    )

    # 6. Check every rule has a check function name
    missing_fn = [r.criterion for r in WCAG_RULES_LEDGER if not r.check_fn_name]
    assert not missing_fn, (
        f"Rules missing check_fn_name: {missing_fn}"
    )

    # 7. Verify expected criteria are present
    expected_criteria = {
        # Level A (30)
        "1.1.1", "1.2.1", "1.2.2", "1.2.3",
        "1.3.1", "1.3.2", "1.3.3",
        "1.4.1", "1.4.2",
        "2.1.1", "2.1.2", "2.1.4",
        "2.2.1", "2.2.2",
        "2.3.1",
        "2.4.1", "2.4.2", "2.4.3", "2.4.4",
        "2.5.1", "2.5.2", "2.5.3", "2.5.4",
        "3.1.1",
        "3.2.1", "3.2.2",
        "3.3.1", "3.3.2",
        "4.1.1", "4.1.2",
        # Level AA (20)
        "1.2.4", "1.2.5",
        "1.3.4", "1.3.5",
        "1.4.3", "1.4.4", "1.4.5", "1.4.10", "1.4.11", "1.4.12", "1.4.13",
        "2.4.5", "2.4.6", "2.4.7",
        "3.1.2",
        "3.2.3", "3.2.4",
        "3.3.3", "3.3.4",
        "4.1.3",
    }
    actual_criteria = set(criteria)
    missing = expected_criteria - actual_criteria
    extra = actual_criteria - expected_criteria
    assert not missing, f"Missing criteria in ledger: {sorted(missing)}"
    assert not extra, f"Extra criteria in ledger: {sorted(extra)}"

    # 8. Cross-reference pdf_techniques and failure_techniques against
    #    the authoritative CRITERION_TO_PDF_TECHNIQUES / CRITERION_TO_FAILURE_TECHNIQUES
    #    maps in wcag_techniques.py.  This is the deterministic validation chain:
    #    if anyone edits one without the other, this import fails immediately.
    from services.common.wcag_techniques import (
        CRITERION_TO_FAILURE_TECHNIQUES,
        CRITERION_TO_PDF_TECHNIQUES,
    )

    for rule in WCAG_RULES_LEDGER:
        expected_pdf = CRITERION_TO_PDF_TECHNIQUES.get(rule.criterion, [])
        assert sorted(rule.pdf_techniques) == sorted(expected_pdf), (
            f"Rule {rule.criterion} pdf_techniques mismatch: "
            f"got {sorted(rule.pdf_techniques)}, expected {sorted(expected_pdf)}"
        )
        expected_fail = CRITERION_TO_FAILURE_TECHNIQUES.get(rule.criterion, [])
        assert sorted(rule.failure_techniques) == sorted(expected_fail), (
            f"Rule {rule.criterion} failure_techniques mismatch: "
            f"got {sorted(rule.failure_techniques)}, expected {sorted(expected_fail)}"
        )

    logger.debug("WCAG rules ledger validated: %d rules OK", len(WCAG_RULES_LEDGER))


# Run validation at import time — fail fast if ledger is broken
_validate_ledger()


# ---------------------------------------------------------------------------
# Convenience lookups
# ---------------------------------------------------------------------------


def get_rule(criterion: str) -> WCAGRule | None:
    """Look up a rule by criterion number (e.g. '1.1.1')."""
    for rule in WCAG_RULES_LEDGER:
        if rule.criterion == criterion:
            return rule
    return None


def get_rules_by_principle(principle: Principle) -> list[WCAGRule]:
    """Get all rules for a given principle."""
    return [r for r in WCAG_RULES_LEDGER if r.principle == principle]


def get_applicable_rules(has_forms: bool = False,
                         has_media: bool = False,
                         has_javascript: bool = False,
                         is_document_set: bool = False) -> list[WCAGRule]:
    """Get rules applicable to this specific PDF's content.

    ALWAYS-applicable rules are always included.
    CONDITIONAL rules are included only when their condition is met.
    NEVER rules produce NOT_APPLICABLE results.
    """
    applicable: list[WCAGRule] = []
    for rule in WCAG_RULES_LEDGER:
        if rule.pdf_applicability == PDFApplicability.ALWAYS:
            applicable.append(rule)
        elif rule.pdf_applicability == PDFApplicability.CONDITIONAL:
            # Determine if condition is met
            cond = rule.condition.lower()
            if "form" in cond and has_forms:
                applicable.append(rule)
            elif "media" in cond or "audio" in cond or "video" in cond:
                if has_media:
                    applicable.append(rule)
            elif "javascript" in cond or "interactive" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "document set" in cond:
                if is_document_set:
                    applicable.append(rule)
            elif "hover" in cond or "focus" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "animated" in cond or "flash" in cond or "moving" in cond:
                if has_media:
                    applicable.append(rule)
            elif "shortcut" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "time" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "pointer" in cond or "gesture" in cond or "motion" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "legal" in cond or "financial" in cond:
                if has_forms:
                    applicable.append(rule)
            elif "status" in cond:
                if has_javascript:
                    applicable.append(rule)
            elif "label" in cond:
                if has_forms:
                    applicable.append(rule)
            elif "validation" in cond or "error" in cond:
                if has_forms:
                    applicable.append(rule)
            else:
                # Unknown condition — include for safety (no silent skip)
                applicable.append(rule)
        # NEVER rules are excluded from applicable but still reported as N/A
    return applicable


def get_all_rules() -> list[WCAGRule]:
    """Return the full ledger."""
    return list(WCAG_RULES_LEDGER)


def get_rule_count() -> dict[str, int]:
    """Return a summary of rule counts."""
    return {
        "total": len(WCAG_RULES_LEDGER),
        "level_a": len([r for r in WCAG_RULES_LEDGER if r.level == RuleLevel.A]),
        "level_aa": len([r for r in WCAG_RULES_LEDGER if r.level == RuleLevel.AA]),
        "always_applicable": len([
            r for r in WCAG_RULES_LEDGER
            if r.pdf_applicability == PDFApplicability.ALWAYS
        ]),
        "conditional": len([
            r for r in WCAG_RULES_LEDGER
            if r.pdf_applicability == PDFApplicability.CONDITIONAL
        ]),
        "never_applicable": len([
            r for r in WCAG_RULES_LEDGER
            if r.pdf_applicability == PDFApplicability.NEVER
        ]),
        "automated": len([
            r for r in WCAG_RULES_LEDGER
            if r.automation == CheckAutomation.AUTOMATED
        ]),
        "semi_automated": len([
            r for r in WCAG_RULES_LEDGER
            if r.automation == CheckAutomation.SEMI_AUTOMATED
        ]),
        "manual": len([
            r for r in WCAG_RULES_LEDGER
            if r.automation == CheckAutomation.MANUAL
        ]),
    }
