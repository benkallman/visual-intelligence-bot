#!/usr/bin/env python3
"""
Regenerate post.txt captions from enriched Obsidian note frontmatter.

Reads the frontmatter of each queue folder's Obsidian note and builds a
stronger, more specific caption using title / artist / date / medium /
culture.  Skips captions that are already non-generic.

Usage:
    python scripts/regenerate_captions_from_notes.py --date today --dry-run
    python scripts/regenerate_captions_from_notes.py --date today --write
    python scripts/regenerate_captions_from_notes.py --date today --pack japanese_wood_historical --write
    python scripts/regenerate_captions_from_notes.py --date today --force --write
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
MAX_CHARS = 280


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
        return json.loads(meta.read_text(encoding="utf-8")).get("pack_id") or ""
    except Exception:
        return ""


def _str(v: object) -> str:
    return str(v or "").strip()


def _is_unknown(s: str) -> bool:
    return s.lower() in ("", "unknown", "anonymous")


def _generate_caption(fm: dict) -> str:
    """Build a metadata-grounded caption from note frontmatter.

    Format:  {title}, {year}. {artist — }{culture }{medium}.
    Falls back gracefully when fields are unknown.
    """
    title = _str(fm.get("title"))
    date_year = _str(fm.get("date_year"))
    date_raw = _str(fm.get("date_raw"))
    artist = _str(fm.get("artist"))
    culture = _str(fm.get("culture"))
    medium = _str(fm.get("medium"))
    period = _str(fm.get("period"))

    # Title + date
    date_clause = ""
    if not _is_unknown(date_year):
        date_clause = f", {date_year}"
    elif date_raw and not _is_unknown(date_raw):
        date_clause = f", {date_raw}"

    first_line = f"{title or 'Untitled'}{date_clause}."

    # Detail clause: artist / medium / culture
    details: list[str] = []
    if artist and not _is_unknown(artist):
        details.append(artist)
    medium_parts: list[str] = []
    if culture and not _is_unknown(culture):
        medium_parts.append(culture)
    if medium and not _is_unknown(medium):
        medium_parts.append(medium.lower())
    elif period and not _is_unknown(period):
        medium_parts.append(period)
    if medium_parts:
        details.append(" ".join(medium_parts))

    detail_clause = " — ".join(details)
    if detail_clause:
        caption = f"{first_line} {detail_clause}."
    else:
        caption = first_line

    return caption[:MAX_CHARS]


def _detect_source_block(post_text: str) -> tuple[str, str]:
    """Split post.txt into (caption_body, source_block).

    source_block is the trailing '\\n—\\nSource: ...' portion if present.
    """
    sep = "\n—\n"
    if sep in post_text:
        body, _, tail = post_text.partition(sep)
        return body.strip(), sep + tail
    return post_text.strip(), ""


def regenerate_for_folder(
    folder: Path,
    date_str: str,
    rank: int,
    force: bool = False,
    dry_run: bool = True,
) -> dict:
    result: dict[str, object] = {"folder": str(folder), "dry_run": dry_run}

    post_path = folder / "post.txt"
    if not post_path.is_file():
        result["action"] = "skipped"
        result["reason"] = "no post.txt"
        return result

    current_text = post_path.read_text(encoding="utf-8").strip()
    caption_body, source_block = _detect_source_block(current_text)

    if not force and not is_generic_caption(caption_body):
        result["action"] = "skipped"
        result["reason"] = "caption is already non-generic"
        result["current"] = caption_body[:80]
        return result

    # Load enriched frontmatter
    meta = _load_all_metadata(folder, ROOT_DIR)
    note_path = find_note_path(meta, folder, date_str, rank, ROOT_DIR)
    existing_fm, _ = _parse_existing_note(note_path)

    # Prefer note frontmatter (it's been enriched); fall back to raw metadata
    fm = existing_fm if existing_fm else meta

    new_caption = _generate_caption(fm)
    new_post = new_caption + source_block

    if len(new_post) > MAX_CHARS:
        # Trim caption body to fit
        budget = MAX_CHARS - len(source_block)
        new_caption = new_caption[:budget - 1] + "…"
        new_post = new_caption + source_block

    result["action"] = "update"
    result["old_caption"] = caption_body[:120]
    result["new_caption"] = new_caption
    result["chars"] = len(new_post)

    if not dry_run:
        post_path.write_text(new_post + "\n", encoding="utf-8")

    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(
        description="Regenerate post.txt captions from enriched Obsidian note frontmatter."
    )
    parser.add_argument("--date", default="today", help="Date (YYYY-MM-DD or 'today')")
    parser.add_argument("--pack", default=None, metavar="PACK_ID")
    parser.add_argument("--force", action="store_true", help="Regenerate even non-generic captions")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="Write post.txt to disk")
    group.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()

    dry_run = not args.write
    date_str = _resolve_date(args.date)

    print(f"[regen] date={date_str}  pack={args.pack or 'all'}  force={args.force}  dry_run={dry_run}")

    rank_folders = _discover_rank_folders(date_str)
    if args.pack:
        rank_folders = [(r, f) for r, f in rank_folders if _read_pack_id(f) == args.pack]

    if not rank_folders:
        print(f"[regen] no export folders found under exports/social/{date_str}/")
        return 0

    updated = skipped = 0
    for rank, folder in rank_folders:
        r = regenerate_for_folder(folder, date_str, rank, args.force, dry_run)
        action = r.get("action")
        prefix = "[DRY RUN] " if dry_run else ""
        if action == "update":
            updated += 1
            old = r.get("old_caption", "")
            new = r.get("new_caption", "")
            chars = r.get("chars", "?")
            print(f"  {prefix}UPDATE  rank={rank:02d}  chars={chars}")
            print(f"    OLD: {old}")
            print(f"    NEW: {new}")
        else:
            skipped += 1
            print(f"  SKIP    rank={rank:02d}  {r.get('reason', '')}")

    print(
        f"\n[regen] done: updated={updated}  skipped={skipped}"
        + ("  (no files written — pass --write to apply)" if dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
