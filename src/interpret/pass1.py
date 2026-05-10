"""
Pass 1: literal description via vision model.
Loads the pass1 prompt from prompts/pass1/literal_description.md.
Returns (parsed_dict, provider_used, model_used).
"""

import json
import os
from typing import Any

from src.providers import complete, LLMRequest, LLMResponse

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass1", "literal_description.md"
)


def _strip_code_fences(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw

    parts = raw.split("```", 2)
    if len(parts) < 2:
        return raw

    raw = parts[1]
    if raw.startswith("json"):
        raw = raw[4:]
    return raw.strip()


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "high": "certain",
        "medium": "probable",
        "low": "uncertain",
        "certain": "certain",
        "probable": "probable",
        "uncertain": "uncertain",
    }
    return mapping.get(text, "uncertain")


def _normalize_elements(elements: Any) -> list[dict]:
    if not isinstance(elements, list):
        return []

    normalized: list[dict] = []
    for item in elements:
        if isinstance(item, dict):
            element_text = str(
                item.get("element")
                or item.get("description")
                or item.get("name")
                or ""
            ).strip()
            if not element_text:
                continue
            normalized.append(
                {
                    "element": element_text,
                    "location": str(item.get("location") or "unspecified").strip(),
                    "confidence": _normalize_confidence(item.get("confidence")),
                }
            )
            continue

        if isinstance(item, str) and item.strip():
            normalized.append(
                {
                    "element": item.strip(),
                    "location": "unspecified",
                    "confidence": "uncertain",
                }
            )

    return normalized


def _normalize_pass1(result: Any) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Pass 1 response was not a JSON object")

    dominant_colors = result.get("dominant_colors", [])
    if not isinstance(dominant_colors, list):
        dominant_colors = []

    composition_notes = result.get("composition_notes")
    if composition_notes is not None and not isinstance(composition_notes, str):
        composition_notes = str(composition_notes)

    return {
        "description": str(result.get("description") or "").strip(),
        "elements": _normalize_elements(result.get("elements", [])),
        "dominant_colors": [
            str(color).strip()
            for color in dominant_colors
            if str(color).strip()
        ],
        "composition_notes": composition_notes,
        "pass1_clean": bool(result.get("pass1_clean", False)),
    }


def run_pass1(image_url: str, image_path: str | None = None) -> tuple[dict, str, str]:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    request = LLMRequest(
        system=system_prompt,
        user_text=(
            "Produce a Pass 1 literal description of this image. "
            "Return valid JSON only, conforming to the pass1 section of the interpretation record schema."
        ),
        image_url=image_url,
        image_path=image_path,
        max_tokens=2048,
        want_json=True,
    )
    response = complete(request)

    raw = _strip_code_fences(response.text)
    result = json.loads(raw)

    return _normalize_pass1(result), response.provider_used, response.model_used
