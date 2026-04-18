"""
Motif extractor: identifies recurring visual structures across a set of Pass 1 records.
Runs on demand against the full records store, not per-image.
"""

import json
import os
import datetime
import anthropic

from src.ingest.record_store import load_all_records

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "motif", "motif_extractor.md"
)
CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "_system_constraints.md"
)
MOTIFS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "records")


def run_motif_extraction() -> list[dict]:
    records = load_all_records()
    if len(records) < 2:
        return []

    inputs = [
        {
            "record_id": r["record_id"],
            "elements": r.get("pass1", {}).get("elements", []),
            "description": r.get("pass1", {}).get("description", ""),
        }
        for r in records
        if r.get("pass1", {}).get("pass1_clean", False)
    ]

    if len(inputs) < 2:
        return []

    with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
        constraints = f.read()
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        extractor_prompt = f.read()

    system = f"{constraints}\n\n{extractor_prompt}"
    client = anthropic.Anthropic()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Extract motifs from these Pass 1 records:\n\n```json\n{json.dumps(inputs, indent=2)}\n```\n\nReturn valid JSON only.",
            }
        ],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    result = json.loads(raw)
    motifs = result.get("motifs", [])

    # Stamp and save
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for m in motifs:
        m["created_at"] = now
        m.setdefault("prompt_pack", [])
        m.setdefault("obsidian_note_path", None)
        m.setdefault("human_reviewed", False)
        _save_motif(m)

    return motifs


def _save_motif(motif: dict) -> str:
    path = os.path.join(MOTIFS_DIR, f"{motif['motif_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(motif, f, indent=2)
    return path
