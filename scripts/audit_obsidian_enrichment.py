#!/usr/bin/env python3
"""
Audit the Obsidian enrichment status of a social queue date.

Prints a summary table showing:
  - queue folders found
  - notes found / missing
  - missing title / artist / date / medium counts
  - generic captions
  - posting status

Usage:
    python scripts/audit_obsidian_enrichment.py --date today
    python scripts/audit_obsidian_enrichment.py --date 2026-05-20
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from src.obsidian.artwork_enrichment import (
    ROOT_DIR as _ROOT_DIR,
    find_note_path,
    is_generic_caption,
    _load_all_metadata,
    _parse_existing_note,
)

SOCIAL_EXPORTS_DIR = ROOT_DIR / "exports" / "social"


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _discover_rank_folders(date_str: str) -> list[tuple[int, Path]]:
    base = SOCIAL_EXPORTS_DIR / date_str
    if not base.is_dir():
        return []
    result = []
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        m = re.match(r"^(\d{2})-", folder.name)
        if m:
            result.append((int(m.group(1)), folder))
    return sorted(result, key=lambda p: p[0])


def _is_unknown(v: object) -> bool:
    return str(v or "").strip().lower() in ("", "unknown", "anonymous")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Audit Obsidian enrichment for a social queue date.")
    parser.add_argument("--date", default="today", help="Date (YYYY-MM-DD or 'today')")
    args = parser.parse_args()

    date_str = _resolve_date(args.date)
    print(f"[audit] date={date_str}\n")

    rank_folders = _discover_rank_folders(date_str)
    if not rank_folders:
        print(f"[audit] no export folders found under exports/social/{date_str}/")
        return 0

    rows: list[dict] = []
    for rank, folder in rank_folders:
        meta = _load_all_metadata(folder, ROOT_DIR)
        note_path = find_note_path(meta, folder, date_str, rank, ROOT_DIR)
        note_exists = note_path.is_file()

        fm, _ = _parse_existing_note(note_path) if note_exists else ({}, "")

        # Read post.txt for caption quality check
        post_txt = folder / "post.txt"
        post_text = post_txt.read_text(encoding="utf-8").strip() if post_txt.is_file() else ""

        rows.append({
            "rank": rank,
            "folder": folder.name,
            "note": note_path.name,
            "note_exists": note_exists,
            "has_title": not _is_unknown(fm.get("title") or meta.get("title")),
            "has_artist": not _is_unknown(fm.get("artist") or meta.get("artist")),
            "has_date": not _is_unknown(fm.get("date_year") or meta.get("date_year") or meta.get("year")),
            "has_medium": not _is_unknown(fm.get("medium") or meta.get("medium")),
            "generic_caption": is_generic_caption(post_text),
            "posted": bool(fm.get("posted")) or bool(fm.get("social_post_url")),
        })

    # Summary
    total = len(rows)
    notes_found = sum(1 for r in rows if r["note_exists"])
    notes_missing = total - notes_found
    missing_title = sum(1 for r in rows if not r["has_title"])
    missing_artist = sum(1 for r in rows if not r["has_artist"])
    missing_date = sum(1 for r in rows if not r["has_date"])
    missing_medium = sum(1 for r in rows if not r["has_medium"])
    generic_caps = sum(1 for r in rows if r["generic_caption"])
    posted_count = sum(1 for r in rows if r["posted"])

    print(f"Queue folders    : {total}")
    print(f"Notes found      : {notes_found}")
    print(f"Notes missing    : {notes_missing}")
    print(f"Missing title    : {missing_title}")
    print(f"Missing artist   : {missing_artist}")
    print(f"Missing date     : {missing_date}")
    print(f"Missing medium   : {missing_medium}")
    print(f"Generic captions : {generic_caps}")
    print(f"Posted           : {posted_count}")
    print()

    # Per-folder table
    col_w = max(len(r["folder"]) for r in rows) + 2
    note_w = max(len(r["note"]) for r in rows) + 2
    header = (
        f"{'Rank':<6}{'Folder':<{col_w}}{'Note':<{note_w}}"
        f"{'Found':<7}{'Title':<7}{'Artist':<8}{'Date':<7}{'Medium':<8}{'Generic':<9}{'Posted'}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        def _yn(v: bool) -> str:
            return "Y" if v else "-"
        print(
            f"{r['rank']:<6}{r['folder']:<{col_w}}{r['note']:<{note_w}}"
            f"{_yn(r['note_exists']):<7}{_yn(r['has_title']):<7}{_yn(r['has_artist']):<8}"
            f"{_yn(r['has_date']):<7}{_yn(r['has_medium']):<8}{_yn(r['generic_caption']):<9}{_yn(r['posted'])}"
        )

    if notes_missing > 0:
        print(f"\n  -> Run: python scripts/enrich_social_queue_notes.py --date {date_str} --write")
    if generic_caps > 0:
        print(f"  -> Run: python scripts/regenerate_captions_from_notes.py --date {date_str} --write")

    return 0


if __name__ == "__main__":
    sys.exit(main())
