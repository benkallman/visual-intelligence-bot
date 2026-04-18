"""
Writes one Obsidian markdown note from an interpretation record + source record.
Output path: obsidian/images/{record_id}.md
"""

import os
import datetime

OBSIDIAN_IMAGES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "obsidian", "images"
)


def write_image_note(interpretation_record: dict, source_record: dict) -> str:
    record_id = interpretation_record["record_id"]
    p1 = interpretation_record.get("pass1", {})
    p2 = interpretation_record.get("pass2", {})
    gov = interpretation_record.get("governance", {})

    elements_md = "\n".join(
        f"- **{e['element']}** — {e['location']} ({e['confidence']})"
        for e in p1.get("elements", [])
    )

    symbolic_md = "\n".join(
        f"- `[SYMBOLIC-CANDIDATE]` **{c['candidate']}** — grounded in: *{c['grounding']}* — confidence: {c['confidence']}"
        for c in p2.get("symbolic_candidates", [])
    ) or "None identified."

    recurrence_md = "\n".join(
        f"- [[{r['record_id']}]] — matched element: *{r['matched_element']}* — strength: {r['match_strength']}"
        for r in p2.get("recurrence_references", [])
    ) or "No recurrence matches identified."

    archive_links = "\n".join(
        f"- [[concepts/{c}]]"
        for c in p2.get("archive_context_used", [])
    )

    colors = ", ".join(p1.get("dominant_colors", [])) or "not recorded"

    title = source_record.get("title") or record_id
    artist = source_record.get("artist") or "Unknown"
    date_created = source_record.get("date_created") or "Unknown"
    medium = source_record.get("medium") or "Unknown"
    source_url = source_record.get("url", "")
    access_date = source_record.get("access_date", datetime.date.today().isoformat())
    rights_flag = source_record.get("rights_flag", "rights_unknown")
    review_status = gov.get("review_status", "pending")
    human_reviewed = str(gov.get("human_reviewed", False)).lower()
    correction_notes = gov.get("correction_notes") or ""

    correction_block = f"- **Corrections**: {correction_notes}\n" if correction_notes else ""

    note = f"""---
type: image-record
record_id: "{record_id}"
source_id: "{source_record['source_id']}"
title: "{title}"
artist: "{artist}"
date_created: "{date_created}"
medium: "{medium}"
source_url: "{source_url}"
access_date: "{access_date}"
rights_flag: "{rights_flag}"
review_status: "{review_status}"
tags: [image-record]
---

# {title}

**Artist**: {artist}
**Date**: {date_created}
**Medium**: {medium}
**Source**: [{source_url}]({source_url})
**Ingested**: {access_date}
**Rights**: {rights_flag}

---

## Pass 1 — Literal Description

{p1.get('description', '[No description produced]')}

### Observable Elements

{elements_md or '(none)'}

### Dominant Colors
{colors}

### Composition
{p1.get('composition_notes') or '(not recorded)'}

---

## Pass 2 — Constrained Interpretation

> All readings below are provisional. No claim here is an assertion.

{p2.get('interpretive_notes', '[No interpretation produced]')}

### Symbolic Candidates

{symbolic_md}

### Uncertainty

{p2.get('uncertainty_notes') or '(not stated)'}

---

## Recurrence

{recurrence_md}

---

## Review

- **Status**: {review_status}
- **Human reviewed**: {human_reviewed}
{correction_block}
---

## Links

- [[concepts/witness]]
- [[concepts/description-vs-inference]]
- [[concepts/interpretive-restraint]]
- [[concepts/symbolic-candidate]]
- [[concepts/prohibited-inferences]]
{archive_links}
"""

    os.makedirs(OBSIDIAN_IMAGES_DIR, exist_ok=True)
    note_path = os.path.join(OBSIDIAN_IMAGES_DIR, f"{record_id}.md")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)

    return note_path
