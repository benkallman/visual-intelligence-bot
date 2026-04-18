# Output — Visual Study Page (Obsidian)

## Role

You are generating a structured visual study page for an Obsidian vault.

## Input

- `motif`: a motif record with elements, variation notes, and source record IDs
- `selected_images`: list of source records with Pass 1 descriptions
- `prompt_pack`: output from the image prompt generator for this motif

## Task

Generate a markdown page with these sections:

1. **Title** — clean, descriptive, not clickbait. Format: `[Motif element] — Visual Study`
2. **Intro** — 2–3 sentences. What the motif is, where it appears, why it was selected. No exaggeration.
3. **Motif Summary** — structured list of defining elements and variation notes
4. **Image List** — each image on one line with record_id, title if known, and a 1-sentence Pass 1 note
5. **Prompt Pack** — the full prompt set, formatted for copy-paste
6. **Related Motifs** — 2–4 suggestions for motifs worth comparing, grounded in shared elements

## Constraints

- No fluff sections
- No claims not grounded in Pass 1 evidence
- No aesthetic evaluation of the images themselves
- Related motifs must be structurally justified, not thematically guessed

## Output

Return valid Obsidian-flavored markdown.
Use `[[wikilinks]]` for cross-references to concept notes and other record IDs.

Front matter required:

```yaml
---
type: visual-study
motif_id: ""
element_count: 0
record_count: 0
tags: [visual-study, motif]
---
```
