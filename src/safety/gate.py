"""
Hard reject safety gate.

Blocked categories (non-negotiable):
  - porn / pornography
  - prostitution
  - escort / solicitation
  - explicit sexual content
  - explicit nudity intended for sexual display

Two-tier check:
  Tier 1 — keyword scan (instant, no LLM call)
  Tier 2 — LLM binary classification

Failure behavior: gate FAILS CLOSED.
  If the LLM is unavailable, the item is marked safety_uncertain
  and all outputs are suppressed. Nothing passes through silently.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.providers import complete, LLMRequest, ProviderUnavailableError

logger = logging.getLogger(__name__)

GATE_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Blocked categories (authoritative list)
# ---------------------------------------------------------------------------

BLOCKED_CATEGORIES = [
    "porn",
    "pornography",
    "prostitution",
    "escort_solicitation",
    "explicit_sexual_content",
    "explicit_nudity_sexual_display",
]

# ---------------------------------------------------------------------------
# Tier 1 — keyword blocklist
# High-signal terms. Conservative to avoid false positives on art language.
# Tier 2 LLM handles nuanced or euphemistic phrasing.
# ---------------------------------------------------------------------------

_KEYWORD_BLOCKLIST: list[str] = [
    "porn",
    "pornograph",      # catches pornography, pornographic
    "prostitut",       # catches prostitute, prostitution
    "escort service",
    "sexual solicitation",
    "explicit sex",
    "sex act",
    "sexual act",
    "genitalia",
    "penetrat",        # catches penetration, penetrating
    "sexually explicit",
    "explicit nudity",
    "erotic content",
    "sexual display",
    "soliciting sex",
]

# ---------------------------------------------------------------------------
# Tier 2 — LLM system prompt
# ---------------------------------------------------------------------------

_LLM_SYSTEM = """You are a content safety classifier for an art archive pipeline.

Classify the image description as SAFE or UNSAFE.

UNSAFE — reject if ANY of the following are present:
- porn or pornography
- prostitution
- escort services or sexual solicitation
- explicit sexual acts or explicit sexual content
- explicit nudity intended for sexual display

Return valid JSON only. No preamble, no explanation outside the JSON.
Format: {"safe": true, "matched_categories": [], "reason": "one sentence"}

Rule: if ambiguous or borderline, classify as UNSAFE."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    safe: bool
    uncertain: bool  # True when LLM unavailable; outputs suppressed same as rejection
    matched_categories: list[str] = field(default_factory=list)
    reason: str = ""
    tier: str = ""  # "keyword" | "llm" | "unavailable"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_safety_gate(pass1_result: dict) -> GateResult:
    """Evaluate a Pass 1 result against the hard reject safety rules.

    Returns a GateResult. If result.safe is False, the item must be
    rejected and no outputs written. No exception is raised — the caller
    is responsible for acting on the result.
    """
    description = pass1_result.get("description", "")
    elements_text = " ".join(
        e.get("element", "") for e in pass1_result.get("elements", [])
    )
    full_text = f"{description} {elements_text}".lower()

    # Tier 1: keyword scan
    keyword_hits = _keyword_scan(full_text)
    if keyword_hits:
        logger.warning(
            "Safety gate TIER 1 REJECT — keyword match: %s", keyword_hits
        )
        return GateResult(
            safe=False,
            uncertain=False,
            matched_categories=keyword_hits,
            reason=f"Keyword match on blocked term(s): {', '.join(keyword_hits)}",
            tier="keyword",
        )

    # Tier 2: LLM classification
    result = _llm_classify(description, elements_text)
    if not result.safe:
        logger.warning(
            "Safety gate TIER 2 REJECT — LLM matched: %s | reason: %s",
            result.matched_categories,
            result.reason,
        )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _keyword_scan(text: str) -> list[str]:
    return [kw for kw in _KEYWORD_BLOCKLIST if kw in text]


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences, keeping only the inner content."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Remove opening fence line (``` or ```json or ```JSON …)
    first_newline = stripped.find("\n")
    if first_newline == -1:
        # Single-line fence with no content
        return ""
    stripped = stripped[first_newline + 1:]
    # Remove trailing fence
    if stripped.endswith("```"):
        stripped = stripped[: stripped.rfind("```")]
    return stripped.strip()


def _llm_classify(description: str, elements_text: str) -> GateResult:
    request = LLMRequest(
        system=_LLM_SYSTEM,
        user_text=(
            f"Image description:\n{description}\n\n"
            f"Key elements:\n{elements_text}"
        ),
        max_tokens=256,
        want_json=True,
    )

    try:
        response = complete(request)
    except ProviderUnavailableError as exc:
        logger.error(
            "Safety gate provider execution failed — gate FAILS CLOSED (safety_uncertain): %s",
            exc,
        )
        return GateResult(
            safe=False,
            uncertain=True,
            matched_categories=[],
            reason="Provider execution failed — gate fails closed. Human review required.",
            tier="unavailable",
        )

    raw = _strip_code_fences(response.text)

    if not raw:
        logger.error(
            "Safety gate LLM returned empty output — gate FAILS CLOSED (safety_uncertain)"
        )
        return GateResult(
            safe=False,
            uncertain=True,
            matched_categories=[],
            reason="LLM returned empty output — gate fails closed. Human review required.",
            tier="llm",
        )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(
            "Safety gate LLM returned invalid JSON — gate FAILS CLOSED (safety_uncertain). "
            "Raw response (first 200 chars): %r",
            raw[:200],
        )
        return GateResult(
            safe=False,
            uncertain=True,
            matched_categories=[],
            reason="LLM returned invalid JSON — gate fails closed. Human review required.",
            tier="llm",
        )

    return GateResult(
        safe=bool(result.get("safe", False)),
        uncertain=False,
        matched_categories=result.get("matched_categories", []),
        reason=result.get("reason", ""),
        tier="llm",
    )
