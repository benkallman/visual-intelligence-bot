# Output — Social Media Batch

## Role

You are preparing a social media batch for one visually unusual image.

## Input

- `literal_description`: Pass 1 description
- `anomaly_types`: from the evaluation record
- `key_elements`: from the evaluation record
- `rarity_score`: float

## Task

Generate:

1. **3 caption options** — each in a distinct register (see caption generator styles). Pick 3 of the 5 that best fit the image's anomaly type.
2. **5 hashtags** — specific to the image's content category, not generic art/photography tags. Avoid: #art #photography #aesthetic #vibes #interesting
3. **One-line hook** — a single sentence that could open a post or thread. Grounded in what is actually visible.
4. **Optional thread continuation** — one idea for what a follow-up post in a thread could show or say. Only include if the image supports it.

## Constraints

- Avoid overused phrasing ("you've never seen", "this is wild", "wait for it")
- Avoid spam tone
- Each output should feel distinct and specific to this image
- Hook must reference a specific observable element, not a general mood

## Output Format

```
CAPTIONS:
1. [caption]
2. [caption]
3. [caption]

HASHTAGS:
#tag1 #tag2 #tag3 #tag4 #tag5

HOOK:
[one sentence]

THREAD CONTINUATION:
[one idea, or "none"]
```

## Note — MVP Scope

This prompt is out of scope for the MVP. It is included here for later use.
Do not wire this to auto-posting. Output is for human review before use.
