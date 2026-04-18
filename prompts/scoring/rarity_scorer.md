# Scoring — Rarity Scorer

## Role

You are scoring a single image's rarity for archive inclusion.

## Input

- `pass1_description`: literal description from Pass 1
- `key_elements`: extracted from Pass 1
- `anomaly_types`: from evaluation Pass 2

## Scoring Dimensions

Evaluate each dimension independently, then compute a weighted score.

| Dimension | Weight | Description |
|---|---|---|
| distribution_likelihood | 0.30 | How broadly distributed is this type of image? Common = low score. |
| visual_uniqueness | 0.30 | How structurally distinct are the elements from typical images in this category? |
| cultural_unfamiliarity | 0.25 | Is this from a context that is under-represented in major image archives? |
| memorability | 0.15 | Would this image be distinguishable from 100 similar images? |

`rarity_score = sum(dimension_score * weight)` for each dimension.

## Failure Modes to Detect

Return `risk_of_being_common` as one of:

- `"low"` — genuinely rare, not seen in major collections
- `"medium"` — could be an undiscovered common type
- `"high"` — likely common, proceed with caution

High common-risk images should be flagged for human review even if rarity_score > 0.5.

## Output

```json
{
  "rarity_score": 0.0,
  "dimension_scores": {
    "distribution_likelihood": 0.0,
    "visual_uniqueness": 0.0,
    "cultural_unfamiliarity": 0.0,
    "memorability": 0.0
  },
  "reason": "one sentence grounded in specific Pass 1 elements",
  "risk_of_being_common": "low|medium|high"
}
```

`reason` must reference at least one specific Pass 1 element.
Do not use: interesting, striking, compelling, unusual, fascinating.
