# Output — Caption Generator

## Role

You are generating short captions for a visually unusual image.

## Input

- `literal_description`: Pass 1 description of the image
- `anomaly_notes`: Pass 2 anomaly fields from the evaluation record

## Task

Generate exactly 5 captions in these styles:

1. **minimal** — stripped to the observable core
2. **absurd** — uses the anomaly to create a dry, unexpected register
3. **mysterious** — withholds rather than explains
4. **observational** — describes what a neutral viewer would notice first
5. **poetic (restrained)** — uses precise language, avoids romantic excess

## Constraints

- Maximum 15 words per caption
- No forced humor
- No generic meme phrases or formats
- No emotional adjectives not grounded in Pass 1 elements
- Style 5 (poetic) must remain literal in its nouns and verbs

## Output

Return a numbered list, one caption per line, style label preceding each:

```
1. minimal: [caption]
2. absurd: [caption]
3. mysterious: [caption]
4. observational: [caption]
5. poetic: [caption]
```
