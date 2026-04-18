---
type: concept
name: "description-vs-inference"
source: "visual-intelligence-archive"
tags: [concept, core]
---

# Description vs Inference

The foundational separation this system enforces.

**Description**: what is directly observable in the image. Verifiable by looking.

**Inference**: a claim that goes beyond what is directly observable. Requires reasoning, context, or assumption.

The two must never appear in the same output field. Pass 1 is description only. Pass 2 is inference only, and all inferences must be marked provisional.

## Common Collapse Errors

| Writes | Should write |
|---|---|
| "a melancholy figure" | "a standing figure, facing away" |
| "sunlight streaming hopefully through the window" | "lighter paint area at window opening" |
| "she is waiting" | "figure positioned adjacent to window" |
| "the empty chair suggests absence" | "a chair, unoccupied, left of center" |

The left column is description collapsed into inference. It is forbidden in Pass 1.

---

## Related Concepts

- [[witness]]
- [[interpretive-restraint]]
- [[symbolic-candidate]]
- [[prohibited-inferences]]
