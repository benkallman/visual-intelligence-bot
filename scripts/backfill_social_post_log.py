#!/usr/bin/env python3
"""
Backfill data/social_post_log.json from known existing manually posted X content.

Use this when you sent a post with post_to_x.py directly and the queue log
does not have a record of it. Running this prevents post_daily_queue.py from
re-queuing the same rank.

Behavior:
- Locates the export folder for the given date/rank.
- Reads image.jpg and post.txt to compute content hashes for duplicate detection.
- Reads metadata.json for source_url.
- Checks that the entry is not already in the log (by date+rank or tweet_id).
- Appends a status="posted_manual_backfill" entry to data/social_post_log.json.

Usage:
    python scripts/backfill_social_post_log.py --date 2026-05-10 --rank 2 --tweet-id 1234567890123
    python scripts/backfill_social_post_log.py --date 2026-05-10 --rank 3 --tweet-id 9876543210 --posted-at 2026-05-10T14:30:00
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SOCIAL_EXPORTS_DIR = ROOT_DIR / "exports" / "social"
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"

TEXT_PREVIEW_MAX = 80


def _load_log() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_log(entries: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_rank_folder(date_str: str, rank: int) -> Path | None:
    base = SOCIAL_EXPORTS_DIR / date_str
    if not base.is_dir():
        return None
    prefix = f"{rank:02d}-"
    matches = sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith(prefix))
    return matches[0] if matches else None


def _image_sha256(image_path: Path) -> str | None:
    try:
        return hashlib.sha256(image_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def main(date_str: str, rank: int, tweet_id: str, posted_at: str | None) -> None:
    log = _load_log()

    # Guard: already logged by date+rank.
    for entry in log:
        if entry.get("date") == date_str and entry.get("rank") == rank:
            print(
                f"[backfill] SKIP: already in log — "
                f"date={date_str} rank={rank} status={entry.get('status', 'posted')}"
            )
            return

    # Guard: already logged by tweet_id.
    for entry in log:
        if entry.get("tweet_id") == tweet_id:
            print(
                f"[backfill] SKIP: tweet_id={tweet_id} already in log — "
                f"date={entry.get('date')} rank={entry.get('rank')}"
            )
            return

    folder = _find_rank_folder(date_str, rank)
    if folder is None:
        print(
            f"[backfill] Error: no export folder for date={date_str} rank={rank} "
            f"(looked in {SOCIAL_EXPORTS_DIR / date_str})",
            file=sys.stderr,
        )
        sys.exit(1)

    image_path = folder / "image.jpg"
    post_path = folder / "post.txt"

    if not image_path.is_file():
        print(f"[backfill] Error: missing image.jpg in {folder}", file=sys.stderr)
        sys.exit(1)
    if not post_path.is_file():
        print(f"[backfill] Error: missing post.txt in {folder}", file=sys.stderr)
        sys.exit(1)

    post_text = post_path.read_text(encoding="utf-8").strip()
    image_hash = _image_sha256(image_path)
    text_hash = hashlib.sha256(_normalize_text(post_text).encode()).hexdigest()

    source_url: str | None = None
    meta_path = folder / "metadata.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source_url = meta.get("source_url") or None
        except Exception:
            pass

    ts = posted_at or datetime.datetime.now().isoformat(timespec="seconds")

    entry = {
        "date": date_str,
        "rank": rank,
        "folder": str(folder),
        "tweet_id": tweet_id,
        "posted_at": ts,
        "text_preview": post_text[:TEXT_PREVIEW_MAX],
        "status": "posted_manual_backfill",
        "image_sha256": image_hash,
        "post_text_sha256": text_hash,
        "source_url": source_url,
    }

    log.append(entry)
    _save_log(log)

    print(f"[backfill] Added: date={date_str} rank={rank} folder={folder.name}")
    print(f"[backfill] tweet_id={tweet_id}")
    print(f"[backfill] posted_at={ts}")
    print(f"[backfill] image_sha256={image_hash}")
    print(f"[backfill] post_text_sha256={text_hash}")
    print(f"[backfill] source_url={source_url}")
    print(f"[backfill] status=posted_manual_backfill")
    print(f"[backfill] log updated: {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill social_post_log.json with a manually posted rank.",
    )
    parser.add_argument("--date", required=True, help="Date of the post: YYYY-MM-DD")
    parser.add_argument("--rank", type=int, required=True, help="Rank number (e.g. 2)")
    parser.add_argument("--tweet-id", required=True, help="X/Twitter post ID")
    parser.add_argument(
        "--posted-at",
        help="ISO timestamp when the post was sent (default: now). "
             "Example: 2026-05-10T15:30:00",
    )
    args = parser.parse_args()

    try:
        date_str = datetime.date.fromisoformat(args.date).isoformat()
    except ValueError:
        parser.error(f"Invalid --date: {args.date!r} — expected YYYY-MM-DD")

    if args.rank <= 0:
        parser.error("--rank must be greater than 0")

    posted_at: str | None = None
    if args.posted_at:
        try:
            posted_at = datetime.datetime.fromisoformat(args.posted_at).isoformat(timespec="seconds")
        except ValueError:
            parser.error(f"Invalid --posted-at: {args.posted_at!r} — expected ISO timestamp")

    try:
        main(date_str=date_str, rank=args.rank, tweet_id=args.tweet_id, posted_at=posted_at)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[backfill] Error: {exc}", file=sys.stderr)
        sys.exit(1)
