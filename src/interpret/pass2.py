"""
Pass 2: constrained interpretation.
Reads Pass 1 output only — does not receive the image again.
Returns (parsed_dict, provider_used, model_used).
"""

import json
import os

from src.providers import complete, LLMRequest

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass2", "constrained_interpretation.md"
)


def run_pass2(pass1_output: dict) -> tuple[dict, str, str]:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    request = LLMRequest(
        system=system_prompt,
        user_text=(
            f"Here is the Pass 1 record:\n\n```json\n{json.dumps(pass1_output, indent=2)}\n```\n\n"
            "Produce a Pass 2 constrained interpretation. "
            "Return valid JSON only, conforming to the pass2 section of the interpretation record schema."
        ),
        max_tokens=2048,
    )
    response = complete(request)

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    result = json.loads(raw)

    for candidate in result.get("symbolic_candidates", []):
        if candidate.get("confidence") == "high":
            candidate["confidence"] = "medium"
            result["prohibited_inference_check"]["passed"] = False
            result["prohibited_inference_check"]["violations"].append({
                "rule": "symbolic-candidate-confidence",
                "offending_text": candidate["candidate"],
            })

    result["pass2_clean"] = result.get("prohibited_inference_check", {}).get("passed", False)

    return result, response.provider_used, response.model_used
