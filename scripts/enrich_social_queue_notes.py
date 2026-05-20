#!/usr/bin/env python3
"""
Create or update Obsidian notes for every queued social image.

For each folder under exports/social/YYYY-MM-DD/RANK-slug/ the script reads
metadata.json, caption.txt, post.txt, and any existing record/candidate data
then writes a structured Obsidian note with source-grounded metadata and
historical context.  No hallucination: all claims are metadata-derived.

Usage:
    python scripts/enrich_social_queue_notes.py --date today --dry-run
    python scripts/enrich_social_queue_notes.py --date today --write
    python scripts/enrich_social_queue_notes.py --date 2026-05-20 --pack japanese_wood_historical --write
    python scripts/enrich_social_queue_notes.py --date today --limit 5 --write
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from src.obsidian.artwork_enrichment import enrich_folder

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


def _read_pack_id(folder: Path) -> str:
    meta = folder / "metadata.json"
    if not meta.is_file():
        return ""
    try:
        import json
        return json.loads(meta.read_text(encoding="utf-8")).get("pack_id") or ""
    except Exception:
        return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Enrich Obsidian notes for the social queue.")
    parser.add_argument("--date", default="today", help="Date (YYYY-MM-DD or 'today')")
    parser.add_argument("--pack", default=None, metavar="PACK_ID", help="Only process this pack_id")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Process at most N folders")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="Write notes to disk")
    group.add_argument("--dry-run", action="store_true", default=True, help="Preview only (default)")
    args = parser.parse_args()

    dry_run = not args.write
    date_str = _resolve_date(args.date)

    print(f"[enrich] date={date_str}  pack={args.pack or 'all'}  dry_run={dry_run}")

    rank_folders = _discover_rank_folders(date_str)
    if not rank_folders:
        print(f"[enrich] no export folders found under exports/social/{date_str}/")
        return 0

    if args.pack:
        rank_folders = [(r, f) for r, f in rank_folders if _read_pack_id(f) == args.pack]

    if args.limit:
        rank_folders = rank_folders[: args.limit]

    created = updated = skipped = errors = 0
    for rank, folder in rank_folders:
        result = enrich_folder(folder, date_str, rank, ROOT_DIR, dry_run=dry_run)
        action = result.get("action", "skipped")
        note = Path(result.get("note_path") or "")
        prefix = "[DRY RUN] " if dry_run else ""

        if action == "create":
            created += 1
            print(f"  {prefix}CREATE  rank={rank:02d}  note={note.name}")
        elif action == "update":
            updated += 1
            print(f"  {prefix}UPDATE  rank={rank:02d}  note={note.name}")
        elif action == "error":
            errors += 1
            print(f"  ERROR   rank={rank:02d}  {result.get('error', '')}")
        else:
            skipped += 1
            print(f"  SKIP    rank={rank:02d}  {result.get('reason', '')}")

    print(
        f"\n[enrich] done: created={created}  updated={updated}  "
        f"skipped={skipped}  errors={errors}"
        + ("  (no files written — pass --write to apply)" if dry_run else "")
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
