"""
Recurrence check: compare new record's Pass 1 elements against all existing records.
Uses the LLM for comparison; returns a list of match objects.
"""

import json
import os

from src.ingest.record_store import load_all_records
from src.providers import complete, LLMRequest, ProviderUnavailableError

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "recurrence", "recurrence_check.md"
)


def run_recurrence_check(interpretation_record: dict) -> list:
    new_elements = interpretation_record.get("pass1", {}).get("elements", [])
    if not new_elements:
        return []

    existing_records = load_all_records()
    if not existing_records:
        return []

    new_id = interpretation_record["record_id"]
    candidates = [
        {"record_id": r["record_id"], "elements": r.get("pass1", {}).get("elements", [])}
        for r in existing_records
        if r["record_id"] != new_id and r.get("pass1", {}).get("elements")
    ]

    if not candidates:
        return []

    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    payload = {
        "new_record_id": new_id,
        "new_elements": new_elements,
        "existing_records": candidates,
    }

    request = LLMRequest(
        system=system_prompt,
        user_text=(
            f"Run a recurrence check on this input:\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
            "Return a JSON array of matches only. Return [] if no matches."
        ),
        max_tokens=1024,
    )

    try:
        response = complete(request)
    except ProviderUnavailableError as exc:
        raise RuntimeError(f"Recurrence check failed — no provider available: {exc}") from exc

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)
