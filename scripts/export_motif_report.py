#!/usr/bin/env python3
"""
Export a human-readable motif memory report.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
MOTIF_MEMORY_PATH = os.path.join(ROOT_DIR, "data", "motifs", "motif_memory.json")
ARCHIVE_CONTEXT_PATH = os.path.join(ROOT_DIR, "data", "archive", "archive_context.json")
EXPORTS_DIR = os.path.join(ROOT_DIR, "exports", "motifs")


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: dict | list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _top_recurring(motifs: list[dict], limit: int = 10) -> list[dict]:
    return sorted(
        motifs,
        key=lambda item: (-int(item.get("count", 0) or 0), -float(item.get("viral_average", 0.0) or 0.0), item.get("label", "")),
    )[:limit]


def _rare_watching(motifs: list[dict], limit: int = 10) -> list[dict]:
    return sorted(
        [item for item in motifs if int(item.get("count", 0) or 0) <= 2],
        key=lambda item: (-float(item.get("rarity_average", 0.0) or 0.0), -int(item.get("count", 0) or 0), item.get("label", "")),
    )[:limit]


def _high_viral(motifs: list[dict], limit: int = 10) -> list[dict]:
    return sorted(
        motifs,
        key=lambda item: (-float(item.get("viral_average", 0.0) or 0.0), -int(item.get("count", 0) or 0), item.get("label", "")),
    )[:limit]


def _archive_aligned(motifs: list[dict], archive_context: dict | None, limit: int = 10) -> list[dict]:
    if not archive_context:
        return []

    archive_text = " ".join(
        str(entry.get("text", ""))
        for section in ("motifs", "patterns", "visual_principles")
        for entry in archive_context.get(section, [])
    ).lower()

    aligned = [
        item for item in motifs
        if str(item.get("label", "")).replace("-", " ") in archive_text or str(item.get("notes", "")).strip()
    ]
    return sorted(
        aligned,
        key=lambda item: (-float(item.get("rarity_average", 0.0) or 0.0), -int(item.get("count", 0) or 0), item.get("label", "")),
    )[:limit]


def _suggested_categories(motifs: list[dict], limit: int = 10) -> list[dict]:
    category_templates = {
        "heraldry": "Heraldic paintings",
        "altar": "Church interiors in painting",
        "portrait": "Portrait paintings",
        "manuscript": "Manuscript illuminations",
        "memento-mori": "Memento mori paintings",
        "religious": "Religious art paintings",
        "banner": "Historical banners in paintings",
        "window": "Window scenes in paintings",
        "bridge": "Bridge scenes in paintings",
        "bird": "Bird symbolism in paintings",
        "crown": "Royal portrait paintings",
        "sword": "Portrait paintings with swords",
        "flower": "Still life paintings with flowers",
    }

    suggestions = []
    seen = set()
    ranked = sorted(
        motifs,
        key=lambda item: (
            float(item.get("rarity_average", 0.0) or 0.0),
            float(item.get("viral_average", 0.0) or 0.0),
            int(item.get("count", 0) or 0),
        ),
        reverse=True,
    )

    for item in ranked:
        label = str(item.get("label", ""))
        category = category_templates.get(label)
        if not category or category in seen:
            continue
        seen.add(category)
        suggestions.append(
            {
                "motif": label,
                "suggested_category": category,
                "reason": (
                    f"Based on rarity_average={float(item.get('rarity_average', 0.0) or 0.0):.4f}, "
                    f"viral_average={float(item.get('viral_average', 0.0) or 0.0):.4f}, "
                    f"count={int(item.get('count', 0) or 0)}"
                ),
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def _render_section(title: str, items: list[dict], formatter) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines += ["No items available.", ""]
        return lines
    for item in items:
        lines.append(formatter(item))
    lines.append("")
    return lines


def _render_markdown(report: dict) -> str:
    lines = [
        f"# Motif Report — {report['date']}",
        "",
        f"Updated at: {report['updated_at']}",
        f"Total motifs: {report['motif_count']}",
        "",
    ]

    lines += _render_section(
        "Top Recurring Motifs",
        report["top_recurring_motifs"],
        lambda item: (
            f"- **{item['label']}** — count={item['count']}, rarity_avg={item['rarity_average']:.4f}, "
            f"viral_avg={item['viral_average']:.4f}"
        ),
    )
    lines += _render_section(
        "Rare Motifs Worth Watching",
        report["rare_motifs_worth_watching"],
        lambda item: (
            f"- **{item['label']}** — rarity_avg={item['rarity_average']:.4f}, count={item['count']}, "
            f"examples: {', '.join(item.get('example_elements', [])[:2]) or '(none)'}"
        ),
    )
    lines += _render_section(
        "High-Viral Motifs",
        report["high_viral_motifs"],
        lambda item: (
            f"- **{item['label']}** — viral_avg={item['viral_average']:.4f}, rarity_avg={item['rarity_average']:.4f}, "
            f"count={item['count']}"
        ),
    )
    lines += _render_section(
        "Motifs Aligned With Archive Context",
        report["archive_aligned_motifs"],
        lambda item: (
            f"- **{item['label']}** — notes: {item.get('notes') or '(no archive note)'}"
        ),
    )
    lines += _render_section(
        "Suggested Next Source Categories",
        report["suggested_next_source_categories"],
        lambda item: (
            f"- **{item['suggested_category']}** — from motif `{item['motif']}`; {item['reason']}"
        ),
    )

    return "\n".join(lines)


def main(date_value: str) -> None:
    date_str = _resolve_date(date_value)
    motif_memory = _load_json(MOTIF_MEMORY_PATH)
    if not motif_memory:
        raise FileNotFoundError(f"Motif memory not found at {MOTIF_MEMORY_PATH}")

    archive_context = _load_json(ARCHIVE_CONTEXT_PATH)
    motifs = motif_memory.get("motifs", [])
    report = {
        "date": date_str,
        "updated_at": motif_memory.get("updated_at", ""),
        "motif_count": len(motifs),
        "top_recurring_motifs": _top_recurring(motifs),
        "rare_motifs_worth_watching": _rare_watching(motifs),
        "high_viral_motifs": _high_viral(motifs),
        "archive_aligned_motifs": _archive_aligned(motifs, archive_context),
        "suggested_next_source_categories": _suggested_categories(motifs),
    }

    export_dir = os.path.join(EXPORTS_DIR, date_str)
    os.makedirs(export_dir, exist_ok=True)
    _save_json(os.path.join(export_dir, "motif-report.json"), report)
    with open(os.path.join(export_dir, "motif-report.md"), "w", encoding="utf-8") as f:
        f.write(_render_markdown(report))

    print(f"[motif-report] Wrote {os.path.join(export_dir, 'motif-report.md')}")
    print(f"[motif-report] Wrote {os.path.join(export_dir, 'motif-report.json')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a human-readable motif memory report.")
    parser.add_argument("--date", default="today", help="Export date in YYYY-MM-DD format or 'today'")
    args = parser.parse_args()
    main(args.date)
