#!/usr/bin/env python3
"""
Export a public-facing digest from accepted archive records.

Usage:
    python scripts/export_public_digest.py --date YYYY-MM-DD
    python scripts/export_public_digest.py --date today
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scoring.rarity_detector import run_rarity_detector, save_rarity_detection_record
from src.scoring.viral_scorer import run_viral_scorer

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
RECORDS_DIR = os.path.join(ROOT_DIR, "data", "records")
SOURCES_DIR = os.path.join(ROOT_DIR, "data", "sources")
RARITY_DIR = os.path.join(ROOT_DIR, "data", "rarity")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports", "nightly")
EXPORTS_DIR = os.path.join(ROOT_DIR, "exports", "public-digests")
_SUCCESS_STATUSES = {"pending", "approved", "corrected"}


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


def _summary_path(date_str: str) -> str:
    return os.path.join(REPORTS_DIR, f"{date_str}-summary.json")


def _record_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'rec_', 1)}.json")


def _source_path(source_id: str) -> str:
    return os.path.join(SOURCES_DIR, f"{source_id}.json")


def _rarity_detection_path(source_id: str) -> str:
    return os.path.join(RARITY_DIR, f"{source_id.replace('src_', 'rdt_', 1)}.json")


def _rarity_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'rar_', 1)}.json")


def _viral_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'vir_', 1)}.json")


def _save_viral_record(record: dict) -> None:
    _save_json(_viral_path(record["source_id"]), record)


def _iter_record_paths() -> list[str]:
    if not os.path.isdir(RECORDS_DIR):
        return []
    return sorted(
        os.path.join(RECORDS_DIR, name)
        for name in os.listdir(RECORDS_DIR)
        if name.startswith("rec_") and name.endswith(".json")
    )


def _accepted_source_ids(date_str: str) -> list[str]:
    summary_path = _summary_path(date_str)
    if os.path.isfile(summary_path):
        summary = _load_json(summary_path) or {}
        ids = [
            item["source_id"]
            for item in summary.get("items", [])
            if item.get("status") == "accepted"
        ]
        if ids:
            return ids

    accepted: list[str] = []
    for path in _iter_record_paths():
        record = _load_json(path) or {}
        if not str(record.get("created_at", "")).startswith(date_str):
            continue
        if record.get("governance", {}).get("review_status") not in _SUCCESS_STATUSES:
            continue
        if not record.get("safety", {}).get("safe", False):
            continue
        accepted.append(record["source_id"])
    return accepted


def _ensure_rarity_record(record: dict, source: dict) -> dict | None:
    rarity = _load_json(_rarity_detection_path(record["source_id"]))
    if rarity is None:
        rarity = run_rarity_detector(record, source)
        if "error" not in rarity:
            save_rarity_detection_record(rarity)
    if rarity and "error" not in rarity:
        return rarity
    return None


def _ensure_viral_record(record: dict, source: dict) -> dict | None:
    viral = _load_json(_viral_path(record["source_id"]))
    if viral is None:
        rarity_for_context = _load_json(_rarity_path(record["source_id"]))
        viral = run_viral_scorer(record, source, rarity_record=rarity_for_context)
        if "error" not in viral:
            _save_viral_record(viral)
    if viral and "error" not in viral:
        return viral
    return None


def _extract_visible_elements(record: dict, limit: int = 3) -> list[str]:
    elements = []
    for entry in record.get("pass1", {}).get("elements", []):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("element") or entry.get("description") or "").strip()
        if text:
            elements.append(text)
        if len(elements) >= limit:
            break
    return elements


def _score_item(record: dict, source: dict, rarity: dict | None, viral: dict | None) -> dict:
    rarity_score = float((rarity or {}).get("rarity_score") or 0.0)
    viral_score = float((viral or {}).get("viral_score") or 0.0)
    brand_fit = float(((viral or {}).get("dimensions") or {}).get("brand_fit") or 0.0)
    combined_score = (0.45 * rarity_score) + (0.35 * viral_score) + (0.20 * brand_fit)

    visible_elements = _extract_visible_elements(record)
    source_line = source.get("artist") or source.get("source_type") or "archive source"

    rarity_dims = (rarity or {}).get("rarity_dimensions") or {}
    strongest_rarity = max(rarity_dims.items(), key=lambda item: item[1])[0] if rarity_dims else "rarity"
    reason_bits = []
    if visible_elements:
        reason_bits.append(f"it centers {visible_elements[0].rstrip('.')}")
    if rarity_score > 0:
        reason_bits.append(f"it scores highest on {strongest_rarity.replace('_', ' ')}")
    if brand_fit > 0.45:
        reason_bits.append("it fits the archive tone without forcing interpretation")
    elif viral_score > 0.45:
        reason_bits.append("it has enough visual hook to travel across public surfaces")

    why_selected = "Selected because " + ", and ".join(reason_bits) + "."

    return {
        "source_id": record["source_id"],
        "title": source.get("title") or record["record_id"],
        "source": source_line,
        "visible_elements": visible_elements,
        "why_selected": why_selected,
        "source_url": source.get("url") or source.get("image_url") or "",
        "combined_score": round(combined_score, 4),
        "rarity_score": round(rarity_score, 4),
        "viral_score": round(viral_score, 4),
        "brand_fit": round(brand_fit, 4),
    }


def _sort_and_select(items: list[dict]) -> list[dict]:
    items.sort(key=lambda item: item["combined_score"], reverse=True)
    return items[:5]


def _digest_intro(date_str: str, count: int) -> str:
    return (
        f"Selected from the visual intelligence archive on {date_str}, these {count} records "
        "balance archive rarity, public readability, and brand fit without leaning on hype."
    )


def _render_digest_md(digest: dict) -> str:
    lines = [
        f"# {digest['title']}",
        "",
        digest["intro"],
        "",
    ]

    for item in digest["items"]:
        lines += [
            f"## {item['title']}",
            f"Source: {item['source']}",
            f"Visible elements: {'; '.join(item['visible_elements']) if item['visible_elements'] else '(none recorded)'}",
            f"Why selected: {item['why_selected']}",
            f"Source URL: {item['source_url']}",
            "",
        ]

    lines += [
        "## Next",
        "",
        "- Follow the visual intelligence archive",
        "- Explore the framework",
        "- Support the work",
        "",
    ]
    return "\n".join(lines)


def main(date_value: str) -> None:
    date_str = _resolve_date(date_value)
    source_ids = _accepted_source_ids(date_str)
    if not source_ids:
        print(f"[digest] No accepted records found for {date_str}.")
        return

    scored_items: list[dict] = []
    for source_id in source_ids:
        record = _load_json(_record_path(source_id))
        source = _load_json(_source_path(source_id))
        if not record or not source:
            continue
        if record.get("governance", {}).get("review_status") not in _SUCCESS_STATUSES:
            continue
        if not record.get("safety", {}).get("safe", False):
            continue

        rarity = _ensure_rarity_record(record, source)
        viral = _ensure_viral_record(record, source)
        scored_items.append(_score_item(record, source, rarity, viral))

    selected = _sort_and_select(scored_items)
    export_dir = os.path.join(EXPORTS_DIR, date_str)
    os.makedirs(export_dir, exist_ok=True)

    digest = {
        "date": date_str,
        "title": f"Visual Intelligence Archive Digest — {date_str}",
        "intro": _digest_intro(date_str, len(selected)),
        "combined_score_formula": "0.45 rarity_score + 0.35 viral_score + 0.20 brand_fit",
        "items": selected,
        "calls_to_action": [
            "Follow the visual intelligence archive",
            "Explore the framework",
            "Support the work",
        ],
    }

    _save_json(os.path.join(export_dir, "digest.json"), digest)
    _save_json(os.path.join(export_dir, "selected-items.json"), selected)
    with open(os.path.join(export_dir, "digest.md"), "w", encoding="utf-8") as f:
        f.write(_render_digest_md(digest))

    print(f"[digest] Wrote {os.path.join(export_dir, 'digest.md')}")
    print(f"[digest] Selected {len(selected)} item(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a public-facing digest from accepted archive records.")
    parser.add_argument("--date", required=True, help="Date to export, in YYYY-MM-DD format or 'today'")
    args = parser.parse_args()
    main(args.date)
