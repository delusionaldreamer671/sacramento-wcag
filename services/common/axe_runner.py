"""Real axe-core accessibility scanning via headless Chromium.

Uses playwright to load HTML content into a headless browser and inject
axe-core for WCAG 2.1 AA validation. Falls back to the custom regex
validator if playwright/chromium is not installed.

Setup:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# axe-core JS source — bundled at install time
_AXE_SCRIPT: str | None = None
_PLAYWRIGHT_AVAILABLE: bool = False

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


def _load_axe_script() -> str:
    """Load axe-core JavaScript source.

    Looks for axe.min.js in common locations:
    1. node_modules/axe-core/axe.min.js (project root npm install)
    2. Bundled in package (future)
    """
    global _AXE_SCRIPT
    if _AXE_SCRIPT is not None:
        return _AXE_SCRIPT

    # Search paths for axe-core
    search_paths = [
        Path("node_modules/axe-core/axe.min.js"),
        Path(__file__).parent.parent.parent / "node_modules" / "axe-core" / "axe.min.js",
    ]

    for p in search_paths:
        if p.exists():
            _AXE_SCRIPT = p.read_text(encoding="utf-8")
            logger.info("Loaded axe-core from: %s (%d chars)", p, len(_AXE_SCRIPT))
            return _AXE_SCRIPT

    raise FileNotFoundError(
        "axe-core not found. Run 'npm install axe-core' in the project root."
    )


def is_available() -> bool:
    """Check if axe-core scanning is available (playwright + axe-core installed)."""
    if not _PLAYWRIGHT_AVAILABLE:
        return False
    try:
        _load_axe_script()
        return True
    except FileNotFoundError:
        return False


def run_axe_scan(html_content: str) -> dict[str, Any]:
    """Run axe-core WCAG 2.1 AA scan against HTML content.

    Returns:
        {
            "violations": [...],    # axe violation objects
            "passes": int,          # number of passing rules
            "incomplete": int,      # rules that need manual review
            "inapplicable": int,    # rules not applicable
            "score": float,         # 0.0-1.0 score
            "engine": "axe-core",
        }

    Raises:
        RuntimeError: if playwright or axe-core is not available
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    axe_script = _load_axe_script()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html_content, wait_until="domcontentloaded")

            # Inject axe-core
            # MEDIUM-4.17: set timeout (ms) so evaluate() does not hang indefinitely.
            # 30 seconds is generous for axe-core injection on any real document.
            page.evaluate(axe_script, timeout=30_000)

            # Run axe with WCAG 2.1 AA ruleset — 30-second timeout for analysis
            raw_results = page.evaluate("""() => {
                return axe.run(document, {
                    runOnly: {
                        type: 'tag',
                        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
                    }
                });
            }""", timeout=30_000)
        finally:
            browser.close()

    return _parse_axe_results(raw_results)


def _parse_axe_results(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse raw axe-core output into a normalized result."""
    violations = raw.get("violations", [])
    passes = raw.get("passes", [])
    incomplete = raw.get("incomplete", [])
    inapplicable = raw.get("inapplicable", [])

    total_rules = len(violations) + len(passes) + len(incomplete)
    score = len(passes) / total_rules if total_rules > 0 else 1.0

    # Simplify violations for JSON serialization
    simplified_violations = []
    for v in violations:
        simplified_violations.append({
            "id": v.get("id", ""),
            "impact": v.get("impact", "minor"),
            "description": v.get("description", ""),
            "help": v.get("help", ""),
            "helpUrl": v.get("helpUrl", ""),
            "tags": v.get("tags", []),
            "nodes_count": len(v.get("nodes", [])),
        })

    return {
        "violations": simplified_violations,
        "passes": len(passes),
        "incomplete": len(incomplete),
        "inapplicable": len(inapplicable),
        "score": round(score, 4),
        "engine": "axe-core",
    }
