---
type: concept
name: "prohibited-inferences"
source: "visual-intelligence-archive"
tags: [concept, core, governance]
---

# Prohibited Inferences

Claims the system must never produce. Violations block record finalization.

## The Prohibited List

| Rule | Forbidden output example |
|---|---|
| No artist intent | "the artist intended this to evoke grief" |
| No viewer emotion | "the viewer feels unease when looking at this" |
| No ungrounded symbolism | "the red cloth symbolizes sacrifice" |
| No narrative assertion | "this scene depicts a farewell" |
| No emotional attribution from posture alone | "the figure is despondent" |
| No biographical inference from image | "this reveals the artist's personal loss" |
| No cultural meaning without archive support | "this motif references Egyptian mythology" |

## Enforcement

Pass 2 must include a `prohibited_inference_check` block. If any violation is found:
- `passed` is set to `false`
- The offending text is recorded
- The record is flagged for human review
- The record cannot be written to Obsidian until the flag is resolved

---

## Related Concepts

- [[description-vs-inference]]
- [[interpretive-restraint]]
- [[symbolic-candidate]]
- [[witness]]
