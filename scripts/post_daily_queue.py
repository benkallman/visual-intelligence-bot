#!/usr/bin/env python3
"""
Daily X posting queue: post the next eligible ranked export for a given date.

Wraps post_to_x.py — reuses its OAuth, media upload, and tweet creation logic
without duplicating it — and adds a persistent log that tracks what has been
posted and enforces minimum spacing between posts.

Behavior:
- Discovers all rank folders under exports/social/<date>/ in rank order.
- Skips ranks already logged in data/social_post_log.json for that date.
- Stops cleanly if the daily max-posts cap is already reached.
- Waits (prints next-allowed time and exits 0) if not enough time has elapsed
  since the last post across any date.
- Posts only the single next eligible rank per invocation.
- Dry-run by default; --send is required to post to X.
- On a successful send, appends one entry to data/social_post_log.json.

The script is designed to be called in a polling loop:

    while ($true) {
        python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180 --send
        Start-Sleep -Seconds 1800
    }

Each invocation either posts one item or exits 0 with a readable reason.

Usage:
    python scripts/post_daily_queue.py --date today
    python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180
    python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180 --send
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

# Ensure project root is on the path before local imports.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.join(_SCRIPTS_DIR, "..")
sys.path.insert(0, _ROOT_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

from src.utils.social_env import load_social_env

# Import shared constants and the two network-heavy helpers from post_to_x so
# OAuth signing, media upload, and tweet creation are not duplicated here.
from post_to_x import (  # noqa: E402
    MAX_POST_CHARS,
    SEND_REQUIRED_VARS,
    _read_post_bundle,
    _send_post,
)

ROOT_DIR = Path(_ROOT_DIR).resolve()
SOCIAL_EXPORTS_DIR = ROOT_DIR / "exports" / "social"
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"

TEXT_PREVIEW_MAX = 80  # characters stored in the log entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    """Return an ISO date string; accept 'today' as shorthand."""
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _discover_rank_folders(date_str: str) -> list[tuple[int, Path]]:
    """Return (rank, folder) pairs sorted by rank for all export folders on date_str.

    Folder names are expected to be "<rank:02d>-<slug>" (e.g. "01-some-art/").
    Folders that do not match the numeric prefix pattern are silently ignored.
    Returns an empty list if the date directory does not exist.
    """
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
    return sorted(result, key=lambda pair: pair[0])


def _load_log() -> list[dict]:
    """Load data/social_post_log.json; return [] if absent or unreadable."""
    if not LOG_PATH.is_file():
        return []
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_log(entries: list[dict]) -> None:
    """Write the log atomically (overwrite); create parent dirs if needed."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(date_value: str, max_posts: int, min_minutes: int, send: bool) -> None:
    """Evaluate queue state and post (or preview) the next eligible rank.

    Exits 0 in all expected non-error states (wait, cap reached, nothing left).
    Exits 1 only on missing credentials or an API error.
    """
    date_str = _resolve_date(date_value)
    print(f"[queue] date={date_str}")

    log = _load_log()
    posted_today = [e for e in log if e.get("date") == date_str]
    posted_ranks_today = {e["rank"] for e in posted_today}
    rank_summary = sorted(posted_ranks_today) if posted_ranks_today else "none"
    print(f"[queue] posted_today={len(posted_today)}/{max_posts}  ranks={rank_summary}")

    if len(posted_today) >= max_posts:
        print(f"[queue] max posts ({max_posts}) already reached for {date_str}. Nothing to do.")
        return

    # Enforce minimum spacing based on the most recent post across all dates,
    # so back-to-back runs of the script in a loop don't fire too quickly.
    if min_minutes > 0:
        all_timestamps = [e["posted_at"] for e in log if e.get("posted_at")]
        if all_timestamps:
            last_dt = datetime.datetime.fromisoformat(max(all_timestamps))
            now = datetime.datetime.now()
            elapsed_minutes = (now - last_dt).total_seconds() / 60
            if elapsed_minutes < min_minutes:
                remaining = int(min_minutes - elapsed_minutes)
                next_allowed = last_dt + datetime.timedelta(minutes=min_minutes)
                print(
                    f"[queue] waiting until {next_allowed.strftime('%Y-%m-%d %H:%M')} "
                    f"({remaining}m remaining)"
                )
                return

    rank_folders = _discover_rank_folders(date_str)
    if not rank_folders:
        print(f"[queue] no export folders found under exports/social/{date_str}/")
        print(f"[queue] run: python scripts/select_best_content.py --top {max_posts} --date {date_str}")
        return

    eligible = [(rank, folder) for rank, folder in rank_folders if rank not in posted_ranks_today]
    if not eligible:
        print(f"[queue] all available ranks already posted for {date_str}.")
        return

    next_rank, next_folder = eligible[0]
    print(f"[queue] next eligible rank={next_rank}  folder={next_folder.name}")

    bundle = _read_post_bundle(date_str, next_rank)
    char_count = len(bundle["text"])
    within_limit = char_count <= MAX_POST_CHARS
    limit_note = (
        f"[within {MAX_POST_CHARS}]" if within_limit
        else f"[EXCEEDS limit by {char_count - MAX_POST_CHARS}]"
    )
    print(f"[queue] characters={char_count} {limit_note}")
    print("[queue] preview:")
    print()
    print(bundle["text"])
    print()

    if not send:
        print("[queue] dry run only. Use --send to post to X.")
        return

    if not within_limit:
        print(f"[queue] post exceeds {MAX_POST_CHARS} chars — aborting send.", file=sys.stderr)
        sys.exit(1)

    # Credentials are loaded only in send mode so dry runs need no env vars.
    resolved = load_social_env(str(ROOT_DIR))
    missing = [key for key in SEND_REQUIRED_VARS if not resolved[key]["present"]]
    if missing:
        print(f"[queue] Error: missing credentials: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    credentials = {key: str(resolved[key]["value"]) for key in SEND_REQUIRED_VARS}
    response = _send_post(bundle, credentials)

    tweet_id = None
    if isinstance(response, dict):
        tweet_id = (response.get("data") or {}).get("id")

    entry = {
        "date": date_str,
        "rank": next_rank,
        "folder": str(next_folder),
        "tweet_id": tweet_id,
        "posted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "text_preview": bundle["text"][:TEXT_PREVIEW_MAX],
    }
    log.append(entry)
    _save_log(log)

    print(f"[queue] posted tweet_id={tweet_id}")
    print(f"[queue] log updated: {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Post the next eligible ranked social export to X."
    )
    parser.add_argument(
        "--date", default="today",
        help="Date to post from: YYYY-MM-DD or 'today' (default: today)",
    )
    parser.add_argument(
        "--max-posts", type=int, default=3,
        help="Maximum posts per day (default: 3)",
    )
    parser.add_argument(
        "--min-minutes-between-posts", type=int, default=180,
        help="Minimum minutes between any two posts (default: 180)",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="Actually send to X (omit for dry run)",
    )
    args = parser.parse_args()
    try:
        main(
            date_value=args.date,
            max_posts=args.max_posts,
            min_minutes=args.min_minutes_between_posts,
            send=args.send,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[queue] Error: {exc}", file=sys.stderr)
        sys.exit(1)
