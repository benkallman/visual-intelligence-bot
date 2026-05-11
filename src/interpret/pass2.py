"""
Pass 2: constrained interpretation.
Reads Pass 1 output only — does not receive the image again.
Returns (parsed_dict, provider_used, model_used).
"""

import ast
import json
import os
from typing import Any

from src.archive.loader import get_archive_context_for_prompt
from src.motifs.memory import get_motif_memory_summary
from src.providers import complete, LLMRequest

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass2", "constrained_interpretation.md"
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


def _parse_json_response(text: str) -> dict:
    cleaned = _strip_code_fences(text)
    candidates = [cleaned]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Pass 2 response was not valid JSON")


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "high":
        return "medium"
    if text in {"low", "medium"}:
        return text
    return "low"


def _normalize_pic(value: Any) -> dict:
    if isinstance(value, dict):
        passed = value.get("passed")
        if passed is None:
            passed = value.get("prohibited_inference_passed", False)

        violations = value.get("violations", [])
        if not isinstance(violations, list):
            violations = []

        normalized_violations = []
        for violation in violations:
            if isinstance(violation, dict):
                rule = str(violation.get("rule") or "unspecified").strip()
                offending = str(violation.get("offending_text") or "").strip()
                if offending:
                    normalized_violations.append(
                        {"rule": rule, "offending_text": offending}
                    )
            elif isinstance(violation, str) and violation.strip():
                normalized_violations.append(
                    {
                        "rule": "model-self-audit",
                        "offending_text": violation.strip(),
                    }
                )

        return {
            "passed": bool(passed),
            "violations": normalized_violations,
        }

    return {
        "passed": bool(value) if isinstance(value, bool) else False,
        "violations": [],
    }


def _normalize_recurrence_references(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []

    normalized: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        matched_element = item.get("matched_element")
        if isinstance(matched_element, dict):
            matched_element = (
                matched_element.get("element")
                or matched_element.get("description")
                or matched_element.get("location")
                or json.dumps(matched_element, ensure_ascii=True)
            )

        strength = str(item.get("match_strength") or "loose").strip().lower()
        if strength not in {"exact", "close", "loose"}:
            strength = "loose"

        normalized.append(
            {
                "record_id": str(item.get("record_id") or "").strip(),
                "matched_element": str(matched_element or "").strip(),
                "match_strength": strength,
                "notes": (
                    str(item.get("notes")).strip()
                    if item.get("notes") is not None
                    else None
                ),
            }
        )

    return [item for item in normalized if item["record_id"] and item["matched_element"]]


def _normalize_pass2(result: Any) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Pass 2 response was not a JSON object")

    pic = _normalize_pic(result.get("prohibited_inference_check"))

    symbolic_candidates = []
    for candidate in result.get("symbolic_candidates", []):
        if not isinstance(candidate, dict):
            continue

        normalized = {
            "candidate": str(candidate.get("candidate") or "").strip(),
            "grounding": str(candidate.get("grounding") or "").strip(),
            "confidence": _normalize_confidence(candidate.get("confidence")),
            "archive_support": (
                str(candidate.get("archive_support")).strip()
                if candidate.get("archive_support") is not None
                else None
            ),
        }
        if not normalized["candidate"] or not normalized["grounding"]:
            continue
        if str(candidate.get("confidence") or "").strip().lower() == "high":
            pic["passed"] = False
            pic["violations"].append(
                {
                    "rule": "symbolic-candidate-confidence",
                    "offending_text": normalized["candidate"],
                }
            )
        symbolic_candidates.append(normalized)

    archive_context_used = result.get("archive_context_used", [])
    if not isinstance(archive_context_used, list):
        archive_context_used = []

    uncertainty_notes = result.get("uncertainty_notes")
    if isinstance(uncertainty_notes, list):
        uncertainty_notes = "\n".join(
            str(note).strip() for note in uncertainty_notes if str(note).strip()
        ) or None
    elif uncertainty_notes is not None:
        uncertainty_notes = str(uncertainty_notes).strip() or None

    normalized = {
        "interpretive_notes": str(result.get("interpretive_notes") or "").strip(),
        "symbolic_candidates": symbolic_candidates,
        "recurrence_references": _normalize_recurrence_references(
            result.get("recurrence_references", [])
        ),
        "archive_context_used": [
            str(item).strip()
            for item in archive_context_used
            if str(item).strip()
        ],
        "prohibited_inference_check": pic,
        "uncertainty_notes": uncertainty_notes,
    }
    normalized["pass2_clean"] = bool(
        normalized["prohibited_inference_check"].get("passed", False)
    )
    return normalized


def _fallback_pass2(reason: str, raw_text: str) -> dict:
    excerpt = _strip_code_fences(raw_text)[:300]
    return {
        "interpretive_notes": f"[BLOCKED: {reason}]",
        "symbolic_candidates": [],
        "recurrence_references": [],
        "archive_context_used": [],
        "prohibited_inference_check": {
            "passed": False,
            "violations": [
                {
                    "rule": "pass2-json-parse",
                    "offending_text": excerpt or reason,
                }
            ],
        },
        "uncertainty_notes": "Pass 2 output could not be parsed into valid JSON.",
        "pass2_clean": False,
    }


def run_pass2(pass1_output: dict) -> tuple[dict, str, str]:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()
    reference_bits = [
        pass1_output.get("description", ""),
        pass1_output.get("composition_notes", ""),
        *(item.get("element", "") for item in pass1_output.get("elements", []) if isinstance(item, dict)),
    ]
    archive_context = get_archive_context_for_prompt(reference_bits)
    motif_memory_summary = get_motif_memory_summary(reference_bits)

    request = LLMRequest(
        system=system_prompt,
        user_text=(
            f"Here is the Pass 1 record:\n\n```json\n{json.dumps(pass1_output, indent=2)}\n```\n\n"
            "Here is archive reference context from visual-intelligence-archive. "
            "Use it only as contextual framing. Do not treat it as evidence that overrides the image-grounded Pass 1 record.\n\n"
            f"```json\n{json.dumps(archive_context, indent=2)}\n```\n\n"
            "Here is motif memory from prior ingests. Use it only as weak contextual memory. "
            "It must not override Pass 1. You may mention recurrence only when it is grounded in visible elements from Pass 1, "
            "not from motif memory alone. Do not claim symbolic certainty.\n\n"
            f"```json\n{json.dumps(motif_memory_summary, indent=2)}\n```\n\n"
            "Produce a Pass 2 constrained interpretation. "
            "Return valid JSON only, conforming to the pass2 section of the interpretation record schema."
        ),
        max_tokens=2048,
        want_json=True,
    )
    response = complete(request)
    try:
        result = _normalize_pass2(_parse_json_response(response.text))
    except ValueError:
        result = _fallback_pass2("Pass 2 model returned malformed JSON", response.text)

    return result, response.provider_used, response.model_used
