"""
Pass 1: literal description via Claude vision.
Loads the pass1 prompt from prompts/pass1/literal_description.md.
"""

import json
import os
import anthropic

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "pass1", "literal_description.md"
)


def run_pass1(image_url: str, model: str) -> dict:
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
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Produce a Pass 1 literal description of this image. Return valid JSON only, conforming to the pass1 section of the interpretation record schema.",
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if model wraps output
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)
