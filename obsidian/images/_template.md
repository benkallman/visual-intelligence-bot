---
type: image-record
record_id: "{{record_id}}"
source_id: "{{source_id}}"
title: "{{title}}"
artist: "{{artist}}"
date_created: "{{date_created}}"
medium: "{{medium}}"
source_url: "{{source_url}}"
access_date: "{{access_date}}"
rarity_score: {{rarity_score}}
reuse_value: "{{reuse_value}}"
anomaly_types: {{anomaly_types}}
rights_flag: "{{rights_flag}}"
review_status: "{{review_status}}"
tags: [image-record]
---

# {{title}}

**Artist**: {{artist}}
**Date**: {{date_created}}
**Medium**: {{medium}}
**Source**: [{{source_url}}]({{source_url}})
**Ingested**: {{access_date}}
**Rights**: {{rights_flag}}

---

## Pass 1 — Literal Description

{{pass1_description}}

### Observable Elements

{{#each pass1_elements}}
- **{{element}}** — {{location}} ({{confidence}})
{{/each}}

### Dominant Colors
{{dominant_colors}}

### Composition
{{composition_notes}}

---

## Pass 2 — Constrained Interpretation

> All readings below are provisional. No claim here is an assertion.

{{interpretive_notes}}

### Symbolic Candidates

{{#each symbolic_candidates}}
- `[SYMBOLIC-CANDIDATE]` **{{candidate}}** — grounded in: *{{grounding}}* — confidence: {{confidence}}
{{/each}}

### Uncertainty

{{uncertainty_notes}}

---

## Recurrence

{{#if recurrence_references}}
{{#each recurrence_references}}
- [[{{record_id}}]] — matched element: *{{matched_element}}* — strength: {{match_strength}}
{{/each}}
{{else}}
No recurrence matches identified.
{{/if}}

---

## Review

- **Status**: {{review_status}}
- **Human reviewed**: {{human_reviewed}}
{{#if correction_notes}}
- **Corrections**: {{correction_notes}}
{{/if}}

---

## Links

- [[concepts/witness]]
- [[concepts/description-vs-inference]]
- [[concepts/interpretive-restraint]]
{{#each archive_context_used}}
- [[concepts/{{this}}]]
{{/each}}
