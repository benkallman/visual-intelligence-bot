"""
Viral scorer: evaluates one accepted image record for publishability.
Loads prompts/scoring/viral_scorer.md as system prompt.
Runs after interpretation is complete.
"""

import datetime
import json
import os

from src.providers import complete, LLMRequest, ProviderUnavailableError

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "scoring", "viral_scorer.md"
)
CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "_system_constraints.md"
)

_DIMENSIONS = (
    "visual_hook",
    "ambiguity",
    "recognizability",
    "novelty",
    "caption_potential",
    "shareability",
    "brand_fit",
)
_USES = {"archive", "social", "prompt-pack", "study-page", "reject"}


def _strip_code_fences(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _clamp_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def run_viral_scorer(interpretation_record: dict, source_record: dict, rarity_record: dict | None = None) -> dict:
    source_id = interpretation_record["source_id"]
    viral_record_id = source_id.replace("src_", "vir_", 1)

    pass1 = interpretation_record.get("pass1", {})
    pass2 = interpretation_record.get("pass2", {})
    key_elements = [
        str(item.get("element") or "").strip()
        for item in pass1.get("elements", [])
        if isinstance(item, dict) and str(item.get("element") or "").strip()
    ]

    with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
        constraints = f.read()
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        scorer_prompt = f.read()

    payload = {
        "title": source_record.get("title"),
        "artist": source_record.get("artist"),
        "pass1_description": pass1.get("description", ""),
        "key_elements": key_elements,
        "composition_notes": pass1.get("composition_notes"),
        "pass2_notes": pass2.get("interpretive_notes", ""),
        "archive_context_used": pass2.get("archive_context_used", []),
        "uncertainty_notes": pass2.get("uncertainty_notes"),
        "rarity_score": (rarity_record or {}).get("rarity_score"),
        "rarity_reason": (rarity_record or {}).get("reason"),
    }

    request = LLMRequest(
        system=f"{constraints}\n\n{scorer_prompt}",
        user_text=f"Score this accepted image record:\n\n```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn valid JSON only.",
        max_tokens=512,
        want_json=True,
    )

    try:
        response = complete(request)
    except ProviderUnavailableError as exc:
        return {
            "viral_record_id": viral_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Provider unavailable: {exc}",
        }

    raw = _strip_code_fences(response.text)
    if not raw:
        return {
            "viral_record_id": viral_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": "Model returned empty response",
        }

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "viral_record_id": viral_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Invalid JSON from model: {exc}",
        }

    dimensions = {
        key: _clamp_score((result.get("dimensions") or {}).get(key))
        for key in _DIMENSIONS
    }
    recommended_use = str(result.get("recommended_use") or "archive").strip().lower()
    if recommended_use not in _USES:
        recommended_use = "archive"

    return {
        "viral_record_id": viral_record_id,
        "source_id": source_id,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": response.provider_used,
        "model": response.model_used,
        "viral_score": _clamp_score(result.get("viral_score")),
        "dimensions": dimensions,
        "reason": str(result.get("reason") or "").strip(),
        "recommended_use": recommended_use,
    }
