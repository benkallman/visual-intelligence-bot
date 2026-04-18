"""
Pass 2: constrained interpretation.
Reads Pass 1 output only — does not receive the image again.
"""

import json
import os
import anthropic

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass2", "constrained_interpretation.md"
)


def run_pass2(pass1_output: dict, model: str) -> dict:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    client = anthropic.Anthropic()

    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Here is the Pass 1 record:\n\n```json\n{json.dumps(pass1_output, indent=2)}\n```\n\nProduce a Pass 2 constrained interpretation. Return valid JSON only, conforming to the pass2 section of the interpretation record schema.",
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

    # Enforce: symbolic candidates may not have confidence=high
    for candidate in result.get("symbolic_candidates", []):
        if candidate.get("confidence") == "high":
            candidate["confidence"] = "medium"
            result["prohibited_inference_check"]["passed"] = False
            result["prohibited_inference_check"]["violations"].append({
                "rule": "symbolic-candidate-confidence",
                "offending_text": candidate["candidate"],
            })

    result["pass2_clean"] = result.get("prohibited_inference_check", {}).get("passed", False)

    return result
