"""
Build evolving motif memory from stored records and scores.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from collections import defaultdict
from typing import Any

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
RECORDS_DIR = os.path.join(ROOT_DIR, "data", "records")
RARITY_DIR = os.path.join(ROOT_DIR, "data", "rarity")
ARCHIVE_CONTEXT_PATH = os.path.join(ROOT_DIR, "data", "archive", "archive_context.json")
MOTIFS_DIR = os.path.join(ROOT_DIR, "data", "motifs")
MOTIF_MEMORY_PATH = os.path.join(MOTIFS_DIR, "motif_memory.json")

_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "there", "their", "what",
    "appears", "seems", "figure", "image", "painting", "portrait", "scene",
    "part", "center", "left", "right", "upper", "lower", "top", "bottom",
    "background", "foreground", "style", "object", "objects", "person",
}

_CANONICAL_ALIASES: dict[str, list[str]] = {
    "heraldry": ["coat of arms", "heraldry", "shield"],
    "altar": ["church", "chapel", "altar"],
    "portrait": ["portrait", "figure", "face"],
    "manuscript": ["book", "manuscript", "writing"],
    "memento-mori": ["skull", "death", "memento mori"],
}

_SECONDARY_KEYWORDS = {
    "heraldry", "altar", "portrait", "manuscript", "memento mori",
    "religious", "threshold", "arch", "crown", "sword", "bird",
    "frame", "window", "horse", "flower", "banner", "ribbon",
}


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_json_paths(directory: str, prefix: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.startswith(prefix) and name.endswith(".json")
    )


def _slugify(text: str) -> str:
    lowered = str(text or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return normalized or "unknown"


def _clean_phrase(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    cleaned = re.sub(r"^[\"'`]+|[\"'`.,;:!?]+$", "", cleaned)
    return cleaned.strip()


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _token_set(text: str) -> set[str]:
    return set(_tokens(text))


def _archive_context() -> dict | None:
    return _load_json(ARCHIVE_CONTEXT_PATH)


def _load_motif_memory() -> dict | None:
    return _load_json(MOTIF_MEMORY_PATH)


def _extract_pass1_labels(record: dict) -> list[str]:
    labels: list[str] = []
    for entry in record.get("pass1", {}).get("elements", []):
        if isinstance(entry, dict):
            text = _clean_phrase(entry.get("element") or entry.get("description") or "")
        else:
            text = _clean_phrase(entry)
        if text:
            labels.append(text)
    return labels


def _extract_archive_context_labels(record: dict) -> list[str]:
    labels = []
    for item in record.get("pass2", {}).get("archive_context_used", []):
        text = _clean_phrase(item)
        if text:
            labels.append(text)
    return labels


def _extract_rarity_labels(rarity_record: dict | None) -> list[str]:
    if not rarity_record:
        return []
    labels = []
    for item in rarity_record.get("rare_elements", []):
        text = _clean_phrase(item)
        if text:
            labels.append(text)
    return labels


def _canonical_from_phrase(text: str) -> tuple[str, str]:
    lowered = str(text or "").lower()
    for canonical, aliases in _CANONICAL_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return canonical, _clean_phrase(text)

    for token in _tokens(text):
        if token in _SECONDARY_KEYWORDS:
            return _slugify(token), _clean_phrase(text)

    tokens = _tokens(text)
    if tokens:
        return _slugify(tokens[0]), _clean_phrase(text)
    return "unknown", _clean_phrase(text)


def _archive_notes_for_label(canonical_label: str, archive_context: dict | None) -> str:
    if not archive_context:
        return ""

    matches: list[str] = []
    search_text = canonical_label.replace("-", " ")
    for section_name in ("motifs", "patterns", "visual_principles"):
        for entry in archive_context.get(section_name, []):
            heading = str(entry.get("heading", "")).strip()
            text = str(entry.get("text", "")).strip()
            if search_text in heading.lower() or search_text in text.lower():
                snippet = text[:180].strip()
                if snippet and snippet not in matches:
                    matches.append(snippet)
            if len(matches) >= 2:
                break
        if len(matches) >= 2:
            break
    return " ".join(matches[:2])


def _compact_motif_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": str(entry.get("label", "")),
        "count": int(entry.get("count", 0) or 0),
        "rarity_average": float(entry.get("rarity_average", 0.0) or 0.0),
        "viral_average": float(entry.get("viral_average", 0.0) or 0.0),
        "example_elements": list(entry.get("example_elements", [])[:2]),
    }


def get_motif_memory_summary(reference: Any, limit: int = 5) -> dict[str, Any] | None:
    memory = _load_motif_memory()
    if not memory:
        return None

    motifs = list(memory.get("motifs", []))
    if not motifs:
        return None

    if isinstance(reference, list):
        reference_text = "\n".join(str(item) for item in reference if item)
    else:
        reference_text = str(reference or "")
    reference_tokens = _token_set(reference_text)

    def score(entry: dict[str, Any]) -> tuple[int, float, float, int]:
        entry_text = " ".join(
            [
                str(entry.get("label", "")),
                " ".join(str(item) for item in entry.get("aliases", [])),
                " ".join(str(item) for item in entry.get("example_elements", [])),
            ]
        )
        overlap = len(reference_tokens & _token_set(entry_text))
        return (
            overlap,
            float(entry.get("rarity_average", 0.0) or 0.0),
            float(entry.get("viral_average", 0.0) or 0.0),
            int(entry.get("count", 0) or 0),
        )

    matched = [
        entry
        for entry in sorted(motifs, key=score, reverse=True)
        if score(entry)[0] > 0
    ][:limit]
    common = sorted(
        motifs,
        key=lambda entry: (
            int(entry.get("count", 0) or 0),
            float(entry.get("viral_average", 0.0) or 0.0),
            str(entry.get("label", "")),
        ),
        reverse=True,
    )[:limit]
    rare = sorted(
        motifs,
        key=lambda entry: (
            float(entry.get("rarity_average", 0.0) or 0.0),
            -int(entry.get("count", 0) or 0),
            str(entry.get("label", "")),
        ),
        reverse=True,
    )[:limit]
    high_viral = sorted(
        motifs,
        key=lambda entry: (
            float(entry.get("viral_average", 0.0) or 0.0),
            int(entry.get("count", 0) or 0),
            str(entry.get("label", "")),
        ),
        reverse=True,
    )[:limit]

    return {
        "updated_at": str(memory.get("updated_at", "")),
        "motif_count": len(motifs),
        "matched_motifs": [_compact_motif_entry(entry) for entry in matched],
        "common_motifs": [_compact_motif_entry(entry) for entry in common],
        "rare_motifs": [_compact_motif_entry(entry) for entry in rare],
        "high_viral_motifs": [_compact_motif_entry(entry) for entry in high_viral],
    }


def _record_score_map() -> tuple[dict[str, dict], dict[str, dict]]:
    rarity_map: dict[str, dict] = {}
    viral_map: dict[str, dict] = {}

    for path in _iter_json_paths(RARITY_DIR, "rdt_"):
        record = _load_json(path)
        if record and record.get("source_id"):
            rarity_map[record["source_id"]] = record

    for path in _iter_json_paths(RECORDS_DIR, "vir_"):
        record = _load_json(path)
        if record and record.get("source_id"):
            viral_map[record["source_id"]] = record

    return rarity_map, viral_map


def build_motif_memory() -> dict[str, Any]:
    archive_context = _archive_context()
    rarity_map, viral_map = _record_score_map()

    motif_state: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "aliases": set(),
            "record_ids": set(),
            "source_ids": set(),
            "rarity_scores": [],
            "viral_scores": [],
            "first_seen": None,
            "last_seen": None,
            "example_elements": [],
            "notes": "",
        }
    )

    for path in _iter_json_paths(RECORDS_DIR, "rec_"):
        record = _load_json(path)
        if not record:
            continue

        record_id = str(record.get("record_id") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        created_at = str(record.get("created_at") or "").strip()
        if not record_id or not source_id:
            continue

        rarity_record = rarity_map.get(source_id)
        viral_record = viral_map.get(source_id)

        candidates: list[tuple[str, str]] = []
        for text in _extract_pass1_labels(record):
            candidates.append(("pass1", text))
        for text in _extract_archive_context_labels(record):
            candidates.append(("pass2", text))
        for text in _extract_rarity_labels(rarity_record):
            candidates.append(("rarity", text))

        seen_for_record: set[str] = set()
        for origin, raw_label in candidates:
            canonical_label, alias = _canonical_from_phrase(raw_label)
            if canonical_label in seen_for_record:
                state = motif_state[canonical_label]
                if alias:
                    state["aliases"].add(alias.lower())
                continue

            seen_for_record.add(canonical_label)
            state = motif_state[canonical_label]
            if alias:
                state["aliases"].add(alias.lower())
            state["record_ids"].add(record_id)
            state["source_ids"].add(source_id)

            rarity_score = float((rarity_record or {}).get("rarity_score") or 0.0)
            viral_score = float((viral_record or {}).get("viral_score") or 0.0)
            state["rarity_scores"].append(rarity_score)
            state["viral_scores"].append(viral_score)

            if created_at:
                if state["first_seen"] is None or created_at < state["first_seen"]:
                    state["first_seen"] = created_at
                if state["last_seen"] is None or created_at > state["last_seen"]:
                    state["last_seen"] = created_at

            example = alias or raw_label
            example = _clean_phrase(example)
            if example and example not in state["example_elements"] and len(state["example_elements"]) < 5:
                state["example_elements"].append(example)

            if not state["notes"]:
                state["notes"] = _archive_notes_for_label(canonical_label, archive_context)

    motifs: list[dict[str, Any]] = []
    for canonical_label, state in motif_state.items():
        record_ids = sorted(state["record_ids"])
        source_ids = sorted(state["source_ids"])
        rarity_scores = state["rarity_scores"]
        viral_scores = state["viral_scores"]
        aliases = sorted(alias for alias in state["aliases"] if _slugify(alias) != canonical_label)

        motifs.append(
            {
                "motif_id": f"mot_{canonical_label}",
                "label": canonical_label,
                "aliases": aliases,
                "count": len(record_ids),
                "record_ids": record_ids,
                "source_ids": source_ids,
                "rarity_average": round(sum(rarity_scores) / len(rarity_scores), 4) if rarity_scores else 0.0,
                "viral_average": round(sum(viral_scores) / len(viral_scores), 4) if viral_scores else 0.0,
                "first_seen": state["first_seen"] or "",
                "last_seen": state["last_seen"] or "",
                "example_elements": state["example_elements"][:5],
                "notes": state["notes"] or "",
            }
        )

    motifs.sort(key=lambda item: (-item["count"], -item["rarity_average"], item["label"]))
    return {
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "motifs": motifs,
    }


def save_motif_memory(memory: dict[str, Any]) -> str:
    os.makedirs(MOTIFS_DIR, exist_ok=True)
    with open(MOTIF_MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)
    return MOTIF_MEMORY_PATH
