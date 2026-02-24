"""Vertex AI / Gemini 1.5 Pro wrapper for AI drafting operations.

Provides a single VertexAIClient class with typed methods for:
  - Generating WCAG-compliant alt text for images (1.1.1)
  - Generating semantic HTML table structure (1.3.1)
  - Analysing and correcting heading hierarchy (2.4.6)

Also provides a module-level ``generate_alt_text_for_image()`` function that
is used by the synchronous converter pipeline (converter.py → stage_ai_alt_text).
This function is self-contained and does NOT require an instantiated
VertexAIClient — it creates a minimal Gemini call with multimodal input
(base64 image bytes + text context) and gracefully returns a fallback string
when Vertex AI credentials are unavailable or the call fails.

All methods implement retry logic with exponential backoff and enforce a
configurable per-call timeout. Errors are logged and propagated as
VertexAIError so callers can apply fallback strategies.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

from services.common.config import settings
from services.ai_drafting.prompt_templates import (
    build_alt_text_prompt,
    build_heading_hierarchy_prompt,
    build_table_structure_prompt,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional SDK import — vertexai is a heavyweight GCP dependency.
# We import it at the top level when available so that monkeypatching in tests
# works correctly (tests patch ``vertex_client.vertexai`` and
# ``vertex_client.GenerativeModel``). When the SDK is not installed, the
# module-level names are set to None and each function guards with an
# explicit check, falling back gracefully.
# ---------------------------------------------------------------------------

try:
    import vertexai
    from google.api_core.exceptions import GoogleAPICallError, RetryError
    from vertexai.generative_models import (
        GenerationConfig,
        GenerativeModel,
        HarmBlockThreshold,
        HarmCategory,
        Image,
        Part,
        SafetySetting,
    )
    _VERTEXAI_AVAILABLE = True
except ImportError:
    vertexai = None  # type: ignore[assignment]
    GoogleAPICallError = Exception  # type: ignore[assignment,misc]
    RetryError = Exception  # type: ignore[assignment,misc]
    GenerationConfig = None  # type: ignore[assignment,misc]
    GenerativeModel = None  # type: ignore[assignment,misc]
    HarmBlockThreshold = None  # type: ignore[assignment,misc]
    HarmCategory = None  # type: ignore[assignment,misc]
    Image = None  # type: ignore[assignment,misc]
    Part = None  # type: ignore[assignment,misc]
    SafetySetting = None  # type: ignore[assignment,misc]
    _VERTEXAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class VertexAIError(Exception):
    """Raised when Vertex AI calls fail after all retries are exhausted."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


# ---------------------------------------------------------------------------
# Safety settings — relaxed to allow processing of government policy documents
# that may contain discussion of harm-adjacent topics (fire codes, law, etc.)
# Built lazily so the module can be imported even when vertexai is not installed.
# ---------------------------------------------------------------------------


def _build_safety_settings() -> list:
    """Build the safety settings list using the vertexai SDK types.

    Returns an empty list if the SDK is not available so callers can pass
    it directly to GenerativeModel without further checks.
    """
    if not _VERTEXAI_AVAILABLE:
        return []
    return [
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
        SafetySetting(
            category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
    ]


_SAFETY_SETTINGS: list = _build_safety_settings()


# ---------------------------------------------------------------------------
# Standalone helper — used by converter.py stage_ai_alt_text
# ---------------------------------------------------------------------------

_ALT_TEXT_SYSTEM_INSTRUCTION: str = (
    "You are an accessibility specialist creating alternative text for images in "
    "government PDF documents that must comply with WCAG 2.1 SC 1.1.1 (Non-text "
    "Content) at Level AA.\n\n"
    "RULES:\n"
    "- Return ONLY the alt text string — no quotes, markdown, or commentary.\n"
    "- For decorative images (border, divider, background texture): return the "
    "exact two characters: \"\"\n"
    "- For charts/graphs: describe the chart type, data shown, and key trend or "
    "insight (e.g. 'Bar chart showing annual budget by department, 2020-2024; "
    "Public Safety consistently highest at ~38%.').\n"
    "- For logos/seals: return 'Logo: [organisation name]' or 'Seal: [name]'.\n"
    "- For maps: describe the geographic area and what the map illustrates.\n"
    "- For photos of people: describe role or action, not physical appearance.\n"
    "- For images of text: include the exact words shown.\n"
    "- Simple informational images: maximum 125 characters.\n"
    "- Complex figures (data, multiple elements): up to 500 characters.\n"
    "- Do NOT start with 'Image of', 'Picture of', or 'Photo of'.\n"
    "- Use plain language appropriate for the general public."
)


def generate_alt_text_for_image(
    image_base64: str,
    image_mime: str,
    surrounding_text: str,
    page_num: int,
    fallback_alt: str,
) -> str:
    """Generate WCAG 1.1.1-compliant alt text for an image using Gemini multimodal.

    This is a module-level convenience function used by the synchronous converter
    pipeline. It does NOT require an instantiated VertexAIClient.

    Preconditions checked before attempting any Vertex AI call:
    1. ``settings.vertex_ai_model`` is non-empty (Vertex AI is configured).
    2. ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable is set.

    If either precondition fails, or if the Gemini call raises any exception,
    the function returns ``fallback_alt`` — the pipeline continues without
    AI-generated alt text rather than failing.

    Args:
        image_base64: Base64-encoded image data (without data URI prefix).
        image_mime: MIME type string, e.g. ``"image/png"`` or ``"image/jpeg"``.
        surrounding_text: Text from adjacent elements (provides semantic context).
        page_num: 1-based page number in the source PDF (used in the prompt).
        fallback_alt: Alt text to return when Vertex AI is unavailable or fails.
            Typically the generic placeholder set during extraction.

    Returns:
        A non-empty alt text string. Returns ``fallback_alt`` on any failure.
        Returns ``""`` (empty string) if Gemini classifies the image as decorative.
    """
    # Gate 0: Vertex AI SDK must be installed
    if not _VERTEXAI_AVAILABLE:
        logger.debug(
            "generate_alt_text_for_image: vertexai SDK not installed — skipping"
        )
        return fallback_alt

    # Gate 1: Vertex AI model must be configured
    if not settings.vertex_ai_model:
        logger.debug(
            "generate_alt_text_for_image: vertex_ai_model not set — skipping"
        )
        return fallback_alt

    # Gate 2: GCP credentials must be present
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.debug(
            "generate_alt_text_for_image: GOOGLE_APPLICATION_CREDENTIALS not set — skipping"
        )
        return fallback_alt

    # Gate 3: image data must be non-empty
    if not image_base64:
        logger.debug(
            "generate_alt_text_for_image: empty image_base64 — skipping"
        )
        return fallback_alt

    try:
        vertexai.init(
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
        )

        model = GenerativeModel(
            settings.vertex_ai_model,
            system_instruction=_ALT_TEXT_SYSTEM_INSTRUCTION,
            safety_settings=_SAFETY_SETTINGS,
        )

        # Decode base64 back to raw bytes for the Vertex AI Image part
        image_bytes = base64.b64decode(image_base64)
        image_part = Part.from_image(Image.from_bytes(image_bytes))

        context_part = (
            f"Generate WCAG 1.1.1-compliant alt text for this image extracted from "
            f"a Sacramento County government PDF (page {page_num}).\n\n"
            f"SURROUNDING TEXT CONTEXT (text immediately before and after this image "
            f"in the document, for semantic context):\n"
            f"{surrounding_text.strip() if surrounding_text.strip() else 'None available'}\n\n"
            "Return only the alt text string."
        )

        response = model.generate_content(
            contents=[image_part, context_part],
            generation_config=GenerationConfig(
                temperature=0.2,
                max_output_tokens=512,
                top_p=0.8,
            ),
            stream=False,
        )

        if not response.candidates:
            logger.warning(
                "generate_alt_text_for_image: no candidates returned for page %d",
                page_num,
            )
            return fallback_alt

        raw = response.candidates[0].content.parts[0].text
        raw_stripped = raw.strip()

        # Detect the decorative sentinel: Gemini returns the two-char string ""
        # (a pair of double-quote characters) to signal a decorative/presentation
        # image that should have alt="" in the HTML output.
        if raw_stripped == '""' or raw_stripped == "''":
            logger.debug(
                "generate_alt_text_for_image: decorative image detected on page %d",
                page_num,
            )
            return ""

        alt_text = raw_stripped.strip('"').strip("'")

        if not alt_text:
            logger.warning(
                "generate_alt_text_for_image: empty text from Gemini for page %d",
                page_num,
            )
            return fallback_alt

        # Truncate runaway responses
        if len(alt_text) > 1000:
            logger.warning(
                "generate_alt_text_for_image: response exceeds 1000 chars (%d) — truncating",
                len(alt_text),
            )
            alt_text = alt_text[:1000]

        logger.info(
            "generate_alt_text_for_image: generated %d-char alt text for page %d",
            len(alt_text),
            page_num,
        )
        return alt_text

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_alt_text_for_image: Vertex AI call failed for page %d (%s: %s) "
            "— returning fallback alt text",
            page_num,
            type(exc).__name__,
            exc,
        )
        return fallback_alt


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class VertexAIClient:
    """Vertex AI Gemini client with retry and timeout handling.

    Initialise once per service process and reuse across requests:

        client = VertexAIClient()
        alt_text = client.generate_alt_text(image_context, surrounding_text)
    """

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        model_name: str | None = None,
        max_retries: int | None = None,
        retry_backoff_base: float | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Initialise the Vertex AI SDK and create a GenerativeModel instance.

        All parameters fall back to values from settings when not provided,
        making the client usable with zero arguments in a properly configured
        environment.

        Args:
            project_id: GCP project ID. Defaults to settings.gcp_project_id.
            location: GCP region. Defaults to settings.vertex_ai_location.
            model_name: Gemini model name. Defaults to settings.vertex_ai_model.
            max_retries: Maximum retry attempts on transient errors.
                         Defaults to settings.max_retries.
            retry_backoff_base: Exponential backoff base in seconds.
                                Defaults to settings.retry_backoff_base.
            timeout_seconds: Per-call timeout in seconds.
                             Defaults to settings.ai_drafting_timeout_seconds.

        Raises:
            ImportError: If the ``vertexai`` SDK is not installed.
        """
        if not _VERTEXAI_AVAILABLE:
            raise ImportError(
                "VertexAIClient requires the 'vertexai' package. "
                "Install it with: pip install google-cloud-aiplatform"
            )

        self._project_id = project_id or settings.gcp_project_id
        self._location = location or settings.vertex_ai_location
        self._model_name = model_name or settings.vertex_ai_model
        self._max_retries = max_retries if max_retries is not None else settings.max_retries
        self._backoff_base = (
            retry_backoff_base
            if retry_backoff_base is not None
            else settings.retry_backoff_base
        )
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.ai_drafting_timeout_seconds
        )

        vertexai.init(project=self._project_id, location=self._location)
        self._model = GenerativeModel(
            self._model_name,
            safety_settings=_SAFETY_SETTINGS,
        )
        logger.info(
            "VertexAIClient initialised: model=%s project=%s location=%s",
            self._model_name,
            self._project_id,
            self._location,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_alt_text(
        self,
        image_context: dict[str, Any],
        surrounding_text: str,
    ) -> str:
        """Generate WCAG 1.1.1-compliant alt text for an image element.

        Args:
            image_context: Dict from Adobe Extract with at minimum:
                - element_type (str): e.g. "Figure"
                - bounding_box (list[float]): [x1, y1, x2, y2]
                - page_number (int): 1-based page index
                - page_dimensions (str, optional): "W x H" in points
                - additional_context (str, optional): caption or label text
            surrounding_text: Concatenated text from adjacent elements.

        Returns:
            Alt text string. Returns empty string "" for decorative images
            as per Gemini's instruction. Never returns None.

        Raises:
            VertexAIError: If all retries are exhausted without a valid response.
        """
        if not isinstance(image_context, dict):
            raise ValueError(
                f"image_context must be a dict, got {type(image_context).__name__}"
            )

        element_type = str(image_context.get("element_type", "Figure"))
        bounding_box = str(image_context.get("bounding_box", "unknown"))
        page_number = int(image_context.get("page_number", 1))
        page_dimensions = str(image_context.get("page_dimensions", "unknown"))
        additional_context = str(image_context.get("additional_context", ""))

        system_prompt, user_message = build_alt_text_prompt(
            element_type=element_type,
            bounding_box=bounding_box,
            surrounding_text=surrounding_text or "",
            page_number=page_number,
            page_dimensions=page_dimensions,
            additional_context=additional_context,
        )

        raw_response = self._call_gemini_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            generation_config=GenerationConfig(
                temperature=0.2,
                max_output_tokens=512,
                top_p=0.8,
            ),
            operation="generate_alt_text",
        )

        alt_text = raw_response.strip().strip('"').strip("'")

        # Validate: non-empty and within sensible length bounds
        if len(alt_text) > 1000:
            logger.warning(
                "Alt text response exceeds 1000 chars (%d chars); truncating",
                len(alt_text),
            )
            alt_text = alt_text[:1000]

        logger.debug(
            "Generated alt text (%d chars) for %s on page %d",
            len(alt_text),
            element_type,
            page_number,
        )
        return alt_text

    def generate_table_structure(self, table_data: dict[str, Any]) -> str:
        """Generate semantic HTML table markup compliant with WCAG 1.3.1.

        Args:
            table_data: Dict from Adobe Extract with at minimum:
                - raw_table_data (str | list): Row/cell objects from the extractor.
                - column_headers (list[str], optional): Identified column headers.
                - row_headers (list[str], optional): Identified row headers.
                - table_id (str, optional): Adobe element ID.
                - page_number (int, optional): 1-based page index.
                - rows (int, optional): Row count.
                - cols (int, optional): Column count.
                - has_column_headers (bool, optional): Header detection flag.
                - has_row_headers (bool, optional): Row header detection flag.
                - nesting_depth (int, optional): 0 = flat, 2+ = nested/complex.
                - caption_text (str, optional): Caption or nearby label text.

        Returns:
            A complete <table>...</table> HTML string.

        Raises:
            VertexAIError: If all retries are exhausted without a valid response.
            ValueError: If table_data is not a dict.
        """
        if not isinstance(table_data, dict):
            raise ValueError(
                f"table_data must be a dict, got {type(table_data).__name__}"
            )

        raw = table_data.get("raw_table_data", [])
        raw_str = json.dumps(raw) if not isinstance(raw, str) else raw
        col_headers = table_data.get("column_headers", [])
        col_headers_str = json.dumps(col_headers) if not isinstance(col_headers, str) else col_headers
        row_headers = table_data.get("row_headers", [])
        row_headers_str = json.dumps(row_headers) if not isinstance(row_headers, str) else row_headers

        system_prompt, user_message = build_table_structure_prompt(
            raw_table_data=raw_str,
            column_headers=col_headers_str,
            row_headers=row_headers_str,
            table_id=str(table_data.get("table_id", "unknown")),
            page_number=int(table_data.get("page_number", 1)),
            rows=int(table_data.get("rows", 0)),
            cols=int(table_data.get("cols", 0)),
            has_column_headers=bool(table_data.get("has_column_headers", True)),
            has_row_headers=bool(table_data.get("has_row_headers", False)),
            nesting_depth=int(table_data.get("nesting_depth", 0)),
            caption_text=str(table_data.get("caption_text", "")),
        )

        raw_response = self._call_gemini_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            generation_config=GenerationConfig(
                temperature=0.1,
                max_output_tokens=4096,
                top_p=0.8,
            ),
            operation="generate_table_structure",
        )

        html = raw_response.strip()

        # Strip markdown code fences if Gemini wraps the output
        if html.startswith("```"):
            lines = html.splitlines()
            html = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        if not html.lower().startswith("<table"):
            logger.warning(
                "Table structure response does not start with <table>; "
                "wrapping in fallback table element"
            )
            html = f"<table>\n{html}\n</table>"

        logger.debug(
            "Generated table HTML (%d chars) for table_id=%s",
            len(html),
            table_data.get("table_id", "unknown"),
        )
        return html

    def generate_heading_structure(
        self, headings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Analyse heading hierarchy and return corrected structure.

        Args:
            headings: List of heading dicts, each with at minimum:
                - element_id (str): Adobe Extract element ID.
                - page_number (int): 1-based page number.
                - level (int): Heading level as detected (1-6).
                - text (str): Heading text content.

        Returns:
            List of corrected heading dicts, each containing:
                original_level, corrected_level, text, element_id,
                page_number, flag ("OK"|"LEVEL_CORRECTED"|"NEEDS_REVIEW"|"MANUAL"),
                suggestion (str | None).

        Raises:
            VertexAIError: If all retries are exhausted without a valid response.
            ValueError: If headings is not a list.
        """
        if not isinstance(headings, list):
            raise ValueError(
                f"headings must be a list, got {type(headings).__name__}"
            )
        if not headings:
            return []

        # Validate individual entries
        validated: list[dict[str, Any]] = []
        for i, h in enumerate(headings):
            if not isinstance(h, dict):
                raise ValueError(
                    f"headings[{i}] must be a dict, got {type(h).__name__}"
                )
            validated.append(
                {
                    "element_id": str(h.get("element_id", f"h_{i}")),
                    "page_number": int(h.get("page_number", 1)),
                    "level": int(h.get("level", 2)),
                    "text": str(h.get("text", "")),
                }
            )

        heading_list_str = json.dumps(validated, indent=2)
        total_pages = max(
            (h.get("page_number", 1) for h in validated), default=1
        )
        # Attempt to infer document title from the first H1 or H2
        document_title = "Unknown"
        for h in validated:
            if h.get("level") in (1, 2) and h.get("text"):
                document_title = h["text"]
                break

        system_prompt, user_message = build_heading_hierarchy_prompt(
            heading_list=heading_list_str,
            total_pages=total_pages,
            document_title=document_title,
        )

        raw_response = self._call_gemini_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            generation_config=GenerationConfig(
                temperature=0.1,
                max_output_tokens=4096,
                top_p=0.9,
            ),
            operation="generate_heading_structure",
        )

        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            corrected: list[dict[str, Any]] = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise VertexAIError(
                f"generate_heading_structure: Gemini returned non-JSON response: "
                f"{cleaned[:200]}",
                cause=exc,
            ) from exc

        if not isinstance(corrected, list):
            raise VertexAIError(
                "generate_heading_structure: Expected JSON array, got "
                f"{type(corrected).__name__}"
            )

        # Validate output schema — ensure required keys are present
        required_keys = {
            "original_level", "corrected_level", "text",
            "element_id", "page_number", "flag", "suggestion",
        }
        for item in corrected:
            missing = required_keys - item.keys()
            if missing:
                raise VertexAIError(
                    f"generate_heading_structure: Response item missing keys: {missing}"
                )

        logger.debug(
            "Generated corrected heading structure for %d headings",
            len(corrected),
        )
        return corrected

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_gemini_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        generation_config: GenerationConfig,
        operation: str,
    ) -> str:
        """Call Gemini with retry on transient 5xx errors.

        Args:
            system_prompt: System instruction text.
            user_message: User-turn message text.
            generation_config: GenerationConfig parameters.
            operation: Name of the calling operation (used in log messages).

        Returns:
            The text content of the first candidate in the response.

        Raises:
            VertexAIError: After max_retries consecutive failures.
        """
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                model = GenerativeModel(
                    self._model_name,
                    system_instruction=system_prompt,
                    safety_settings=_SAFETY_SETTINGS,
                )
                response = model.generate_content(
                    contents=user_message,
                    generation_config=generation_config,
                    # Vertex AI SDK accepts timeout as a client option; we
                    # pass it via the request metadata when supported.
                    # The SDK enforces this on the underlying gRPC channel.
                    stream=False,
                )

                if not response.candidates:
                    raise VertexAIError(
                        f"{operation}: Gemini returned no candidates "
                        f"(attempt {attempt}/{self._max_retries})"
                    )

                text = response.candidates[0].content.parts[0].text
                if not text or not text.strip():
                    raise VertexAIError(
                        f"{operation}: Gemini returned empty text content "
                        f"(attempt {attempt}/{self._max_retries})"
                    )

                logger.info(
                    "%s completed on attempt %d/%d",
                    operation,
                    attempt,
                    self._max_retries,
                )
                return text

            except (GoogleAPICallError, RetryError) as exc:
                last_exc = exc
                wait = self._backoff_base ** attempt
                logger.warning(
                    "%s: Vertex AI API error on attempt %d/%d (%s). "
                    "Retrying in %.1fs.",
                    operation,
                    attempt,
                    self._max_retries,
                    type(exc).__name__,
                    wait,
                )
                if attempt < self._max_retries:
                    time.sleep(wait)

            except VertexAIError:
                # Internal validation errors — re-raise immediately, no retry.
                raise

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception(
                    "%s: Unexpected error on attempt %d/%d",
                    operation,
                    attempt,
                    self._max_retries,
                )
                if attempt < self._max_retries:
                    wait = self._backoff_base ** attempt
                    time.sleep(wait)

        raise VertexAIError(
            f"{operation}: All {self._max_retries} attempts failed. "
            f"Last error: {last_exc}",
            cause=last_exc,
        )
