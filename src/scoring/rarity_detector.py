"""
Rarity detector: compares one image record against local archive context.
Loads prompts/scoring/rarity_detector.md as system prompt.
Runs after Pass 1 and recurrence are complete.
"""

import datetime
import json
import os
import re
from urllib.parse import urlparse

from src.providers import complete, LLMRequest, ProviderUnavailableError

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "scoring", "rarity_detector.md"
)
CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "_system_constraints.md"
)
RECORDS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "records")
SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sources")
RARITY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "rarity")

_DIMENSIONS = (
    "source_rarity",
    "subject_rarity",
    "composition_rarity",
    "context_rarity",
    "archive_rarity",
    "motif_rarity",
)
_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "there", "their", "what",
    "seems", "appears", "figure", "image", "foreground", "background",
    "center", "right", "left", "upper", "lower", "front", "back",
}


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


def _extract_label(entry) -> str | None:
    if isinstance(entry, str):
        text = entry.strip()
        return text or None
    if isinstance(entry, dict):
        for key in ("element", "description", "name", "label", "item"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    }
    return tokens


def _element_overlap(a: str, b: str) -> int:
    return len(_tokens(a) & _tokens(b))


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_records(prefix: str) -> list[dict]:
    if not os.path.isdir(RECORDS_DIR):
        return []
    records: list[dict] = []
    for name in os.listdir(RECORDS_DIR):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        record = _load_json(os.path.join(RECORDS_DIR, name))
        if record is not None:
            records.append(record)
    return records


def _source_record(source_id: str) -> dict | None:
    return _load_json(os.path.join(SOURCES_DIR, f"{source_id}.json"))


def _source_domain(source_record: dict) -> str:
    url = source_record.get("url") or source_record.get("image_url") or ""
    return urlparse(url).netloc.lower()


def _build_payload(interpretation_record: dict, source_record: dict) -> tuple[dict, list[str], list[str]]:
    current_record_id = interpretation_record["record_id"]
    current_source_id = interpretation_record["source_id"]
    new_elements = [
        label
        for entry in interpretation_record.get("pass1", {}).get("elements", [])
        if (label := _extract_label(entry))
    ]

    existing_records = [
        record
        for record in _load_records("rec_")
        if record.get("record_id") != current_record_id and record.get("pass1", {}).get("elements")
    ]
    motif_records = _load_records("mot_")

    existing_sources = [
        source
        for record in existing_records
        if (source := _source_record(record.get("source_id", "")))
    ]

    current_domain = _source_domain(source_record)
    current_artist = str(source_record.get("artist") or "").strip().lower()
    domain_frequency = sum(1 for source in existing_sources if _source_domain(source) == current_domain)
    artist_frequency = sum(
        1
        for source in existing_sources
        if current_artist and str(source.get("artist") or "").strip().lower() == current_artist
    )

    element_stats = []
    rare_elements = []
    common_elements = []
    for element in new_elements:
        repeated_in = 0
        sample_records: list[str] = []
        for record in existing_records:
            matched = False
            for existing in record.get("pass1", {}).get("elements", []):
                existing_label = _extract_label(existing)
                if existing_label and _element_overlap(element, existing_label) >= 1:
                    repeated_in += 1
                    sample_records.append(record["record_id"])
                    matched = True
                    break
            if matched and len(sample_records) >= 3:
                continue
        element_stats.append({
            "element": element,
            "repeated_in_records": repeated_in,
            "sample_record_ids": sample_records[:3],
        })
        if repeated_in == 0:
            rare_elements.append(element)
        elif repeated_in >= 2:
            common_elements.append(element)

    new_token_union = set().union(*(_tokens(element) for element in new_elements)) if new_elements else set()
    motif_hits = []
    for motif in motif_records:
        motif_tokens = set().union(*(_tokens(element) for element in motif.get("elements", [])))
        if len(new_token_union & motif_tokens) >= 1:
            motif_hits.append({
                "motif_id": motif.get("motif_id"),
                "elements": motif.get("elements", []),
                "recurrence_count": motif.get("recurrence_count", 0),
                "confidence": motif.get("confidence", 0),
            })

    recurrence_refs = [
        {
            "record_id": ref.get("record_id"),
            "matched_element": ref.get("matched_element"),
            "match_strength": ref.get("match_strength"),
        }
        for ref in interpretation_record.get("pass2", {}).get("recurrence_references", [])
        if isinstance(ref, dict)
    ]

    payload = {
        "source_metadata": {
            "source_id": current_source_id,
            "title": source_record.get("title"),
            "artist": source_record.get("artist"),
            "source_type": source_record.get("source_type"),
            "source_domain": current_domain,
        },
        "pass1_description": interpretation_record.get("pass1", {}).get("description", ""),
        "key_elements": new_elements,
        "composition_notes": interpretation_record.get("pass1", {}).get("composition_notes"),
        "local_archive_stats": {
            "existing_record_count": len(existing_records),
            "source_domain_frequency": domain_frequency,
            "artist_frequency": artist_frequency,
            "element_repetition": element_stats,
        },
        "recurrence_matches": recurrence_refs,
        "motif_matches": motif_hits,
    }
    return payload, rare_elements[:5], common_elements[:5]


def run_rarity_detector(interpretation_record: dict, source_record: dict) -> dict:
    source_id = interpretation_record["source_id"]
    record_id = source_id.replace("src_", "rdt_", 1)
    payload, seed_rare, seed_common = _build_payload(interpretation_record, source_record)

    with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
        constraints = f.read()
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        scorer_prompt = f.read()

    request = LLMRequest(
        system=f"{constraints}\n\n{scorer_prompt}",
        user_text=f"Evaluate rarity for this record:\n\n```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn valid JSON only.",
        max_tokens=700,
        want_json=True,
    )

    try:
        response = complete(request)
    except ProviderUnavailableError as exc:
        return {
            "rarity_detection_record_id": record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Provider unavailable: {exc}",
        }

    raw = _strip_code_fences(response.text)
    if not raw:
        return {
            "rarity_detection_record_id": record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": "Model returned empty response",
        }

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "rarity_detection_record_id": record_id,
            "source_id": source_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "error": f"Invalid JSON from model: {exc}",
        }

    dimensions = {
        key: _clamp_score((result.get("rarity_dimensions") or {}).get(key))
        for key in _DIMENSIONS
    }
    rare_elements = [
        str(item).strip()
        for item in result.get("rare_elements", [])
        if str(item).strip()
    ] or seed_rare
    common_elements = [
        str(item).strip()
        for item in result.get("common_elements", [])
        if str(item).strip()
    ] or seed_common

    return {
        "rarity_detection_record_id": record_id,
        "source_id": source_id,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": response.provider_used,
        "model": response.model_used,
        "rarity_score": _clamp_score(result.get("rarity_score")),
        "rarity_dimensions": dimensions,
        "rare_elements": rare_elements[:10],
        "common_elements": common_elements[:10],
        "reason": str(result.get("reason") or "").strip(),
        "confidence": _clamp_score(result.get("confidence")),
    }


def save_rarity_detection_record(record: dict) -> str:
    os.makedirs(RARITY_DIR, exist_ok=True)
    path = os.path.join(RARITY_DIR, f"{record['rarity_detection_record_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path
