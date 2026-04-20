"""
Pass 1: literal description via vision model.
Loads the pass1 prompt from prompts/pass1/literal_description.md.
Returns (parsed_dict, provider_used, model_used).
"""

import json
import os

from src.providers import complete, LLMRequest, LLMResponse

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass1", "literal_description.md"
)


def run_pass1(image_url: str) -> tuple[dict, str, str]:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    request = LLMRequest(
        system=system_prompt,
        user_text=(
            "Produce a Pass 1 literal description of this image. "
            "Return valid JSON only, conforming to the pass1 section of the interpretation record schema."
        ),
        image_url=image_url,
        max_tokens=2048,
        want_json=True,
    )
    response = complete(request)

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw), response.provider_used, response.model_used
