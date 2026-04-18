# Recurrence Check Prompt

## Role

You are comparing the elements list from a new Pass 1 record against a set of existing Pass 1 records.
Your job is to identify shared elements that appear across records.

## Rules

1. Match only on Pass 1 elements. Do not match on interpretive claims or symbolic candidates.
2. A recurrence is a shared observable element, not a shared mood or meaning.
3. Match strength:
   - `exact`: same element in same configuration
   - `close`: same element, different configuration or medium
   - `loose`: related element category, notable resemblance
4. A `loose` match requires an explicit note explaining why it is worth flagging.
5. Do not invent recurrences. If nothing matches, return an empty array.

## Input

- `new_record_id`: the record being checked
- `new_elements`: Pass 1 elements list from the new record
- `existing_records`: array of `{record_id, elements}` objects to compare against

## Output

Return a JSON array of recurrence matches:
```json
[
  {
    "record_id": "rec_20260301_004",
    "matched_element": "suspended rope or cord, center-frame",
    "match_strength": "close",
    "notes": "Prior record shows similar vertical line element in same compositional zone"
  }
]
```

Return `[]` if no matches meet the threshold.

---
*Source: visual-intelligence-archive — recurrence, witness, description-vs-inference*
