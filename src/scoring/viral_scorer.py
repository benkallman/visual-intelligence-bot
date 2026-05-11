"""
Viral scorer: evaluates one accepted image record for publishability.
Loads prompts/scoring/viral_scorer.md as system prompt.
Runs after interpretation is complete.
"""

import datetime
import json
import os
import re
from src.providers import complete, LLMRequest, ProviderUnavailableError

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "scoring", "viral_scorer.md"
)
CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "_system_constraints.md"
)
ARCHIVE_CONTEXT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "archive", "archive_context.json"
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


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2
    }


def _archive_summary(reference_bits: list[str]) -> dict | None:
    archive_context = _load_json(ARCHIVE_CONTEXT_PATH)
    if not archive_context:
        return None

    motifs = archive_context.get("motifs", [])
    patterns = archive_context.get("patterns", [])
    visual_principles = archive_context.get("visual_principles", [])
    print(f"[archive] scoring context loaded: {len(motifs)} motifs, {len(patterns)} patterns")

    reference_tokens = _tokens(" ".join(bit for bit in reference_bits if bit))

    def select(entries: list[dict], limit: int = 3) -> list[dict]:
        ranked = sorted(
            entries,
            key=lambda entry: (
                len(reference_tokens & _tokens(" ".join([
                    str(entry.get("heading", "")),
                    str(entry.get("text", "")),
                    str(entry.get("file", "")),
                ]))),
                len(_tokens(str(entry.get("text", "")))),
            ),
            reverse=True,
        )
        picked: list[dict] = []
        for entry in ranked:
            compact = {
                "file": str(entry.get("file", "")),
                "heading": str(entry.get("heading", "")),
                "text": str(entry.get("text", ""))[:220],
            }
            if compact not in picked:
                picked.append(compact)
            if len(picked) >= limit:
                break
        return picked

    return {
        "files_loaded": int(archive_context.get("files_loaded", 0) or 0),
        "motif_count": len(motifs),
        "pattern_count": len(patterns),
        "visual_principle_count": len(visual_principles),
        "motif_examples": select(motifs),
        "pattern_examples": select(patterns),
        "visual_principle_examples": select(visual_principles),
        "usage_note": (
            "Reference only. Archive context may inform brand_fit, motif framing, and recommended_use, "
            "but must not override the pass1 literal description or other image-grounded evidence."
        ),
    }


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
        "archive_context_summary": _archive_summary(
            [
                pass1.get("description", ""),
                pass1.get("composition_notes", ""),
                pass2.get("interpretive_notes", ""),
                *(key_elements or []),
            ]
        ),
    }

    request = LLMRequest(
        system=f"{constraints}\n\n{scorer_prompt}",
        user_text=(
            "Score this accepted image record. Archive context summary is optional reference material only. "
            "It may influence brand_fit, motif framing, and recommended_use, but it must not override the pass1 "
            "literal description or other image-grounded evidence.\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn valid JSON only."
        ),
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
