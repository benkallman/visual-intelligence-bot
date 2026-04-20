"""
Rarity scorer: evaluates one image record against the rarity scoring rubric.
Loads prompts/scoring/rarity_scorer.md as system prompt.
Runs after Pass 1 is complete.
"""

import json
import os
import datetime

from src.providers import complete, LLMRequest, ProviderUnavailableError

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "scoring", "rarity_scorer.md"
)
CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "_system_constraints.md"
)

WEIGHTS = {
    "distribution_likelihood": 0.30,
    "visual_uniqueness": 0.30,
    "cultural_unfamiliarity": 0.25,
    "memorability": 0.15,
}


def run_rarity_scorer(interpretation_record: dict) -> dict:
    source_id = interpretation_record["source_id"]
    rarity_record_id = source_id.replace("src_", "rar_")

    pass1 = interpretation_record.get("pass1", {})
    pass1_description = pass1.get("description", "")
    _ELEMENT_KEYS = ("element", "name", "item", "label", "description")

    def _extract_label(entry) -> str | None:
        if isinstance(entry, str):
            return entry.strip() or None
        if isinstance(entry, dict):
            for k in _ELEMENT_KEYS:
                v = entry.get(k)
                if v and isinstance(v, str):
                    return v.strip()
        return None

    key_elements = [
        label
        for e in pass1.get("elements", [])
        if (label := _extract_label(e))
    ]
    anomaly_types = []

    with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
        constraints = f.read()
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        scorer_prompt = f.read()

    payload = {
        "pass1_description": pass1_description,
        "key_elements": key_elements,
        "anomaly_types": anomaly_types,
    }

    request = LLMRequest(
        system=f"{constraints}\n\n{scorer_prompt}",
        user_text=f"Score this image record:\n\n```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn valid JSON only.",
        max_tokens=512,
        want_json=True,
    )

    try:
        response = complete(request)
    except ProviderUnavailableError as exc:
        return {
            "rarity_record_id": rarity_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Provider unavailable: {exc}",
        }

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    if not raw:
        return {
            "rarity_record_id": rarity_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": "Model returned empty response",
        }

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "rarity_record_id": rarity_record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Invalid JSON from model: {exc}",
        }

    return {
        "rarity_record_id": rarity_record_id,
        "source_id": source_id,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": response.provider_used,
        "model": response.model_used,
        "keep": result.get("rarity_score", 0) >= 0.4 and result.get("risk_of_being_common") != "high",
        "rarity_score": result.get("rarity_score", 0),
        "dimension_scores": result.get("dimension_scores", {}),
        "anomaly_types": result.get("anomaly_types", []),
        "key_elements": key_elements,
        "reuse_value": result.get("reuse_value", "low"),
        "reason": result.get("reason", ""),
        "risk_of_being_common": result.get("risk_of_being_common", "medium"),
        "human_review_required": (
            result.get("risk_of_being_common") == "high"
            or result.get("rarity_score", 0) < 0.4
        ),
    }
