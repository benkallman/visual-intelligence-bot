---
type: visual-study
motif_id: "{{motif_id}}"
element_count: {{element_count}}
record_count: {{recurrence_count}}
tags: [visual-study, motif]
---

# {{motif_name}} — Visual Study

{{intro_2_3_sentences}}

---

## Motif Summary

**Defining elements:**
{{#each elements}}
- {{this}}
{{/each}}

**Variation across instances:**
{{#each variation_notes}}
- {{this}}
{{/each}}

**Confidence**: {{confidence}}

---

## Image Records

{{#each source_record_ids}}
- [[images/{{this}}]] — {{pass1_note}}
{{/each}}

---

## Prompt Pack

{{#each prompt_pack}}
{{@index_plus_one}}. {{this}}
{{/each}}

---

## Related Motifs

{{related_motif_suggestions}}

---

## Links

- [[concepts/recurrence]]
- [[concepts/witness]]
- [[concepts/description-vs-inference]]
