#!/usr/bin/env python3
"""
Daily X posting queue: post the next eligible ranked export for a given date.

Wraps post_to_x.py — reuses its OAuth, media upload, and tweet creation logic
without duplicating it — and adds a persistent log that tracks what has been
posted and enforces minimum spacing between posts.

Behavior:
- Discovers all rank folders under exports/social/<date>/ in rank order.
- Skips ranks already logged in data/social_post_log.json for that date
  (including ranks marked skipped_duplicate or skipped_over_280).
- Stops cleanly if the daily max-posts cap is already reached.
- Waits (prints next-allowed time and exits 0) if not enough time has elapsed
  since the last post across any date.
- Before posting, checks for duplicate content (folder slug, image hash,
  source URL, post text) against the full log and skips if a match is found.
- If post.txt exceeds 280 chars, repairs it in-place using metadata.json then
  continues; skips the rank if repair is not possible.
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
import hashlib
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

TEXT_PREVIEW_MAX = 80
HASHTAGS = "#art #visualintelligence #rareimage"

# Statuses that count toward the daily post cap.
_POSTED_STATUSES = frozenset({"posted", "posted_manual_backfill"})
# Statuses that mean "do not attempt this rank again".
_DONE_STATUSES = frozenset({"posted", "posted_manual_backfill", "skipped_duplicate", "skipped_over_280"})


# ---------------------------------------------------------------------------
# Log I/O
# ---------------------------------------------------------------------------

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
# Folder discovery
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    """Return an ISO date string; accept 'today' as shorthand."""
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _discover_rank_folders(date_str: str) -> list[tuple[int, Path]]:
    """Return (rank, folder) pairs sorted by rank for all export folders on date_str."""
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


# ---------------------------------------------------------------------------
# Duplicate detection helpers
# ---------------------------------------------------------------------------

def _folder_slug(folder_path) -> str:
    """Strip the rank prefix (e.g. '01-') from a social export folder name."""
    return re.sub(r"^\d{2}-", "", Path(folder_path).name)


def _image_sha256(image_path: Path) -> str | None:
    try:
        return hashlib.sha256(image_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_source_url(folder: Path) -> str | None:
    meta = folder / "metadata.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return data.get("source_url") or None
    except Exception:
        return None


def _normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace for stable text comparison."""
    return " ".join(text.strip().lower().split())


def _build_dup_signals(log: list[dict]) -> dict:
    """Build sets of content signals from all posted (not skipped) log entries.

    For old entries that lack pre-computed hash fields, the original files are
    read from disk if still present. This runs once per invocation and is cheap
    since the log is small.
    """
    slugs: set[str] = set()
    image_hashes: set[str] = set()
    source_urls: set[str] = set()
    text_hashes: set[str] = set()

    for entry in log:
        # Only build signals from actually-posted entries.
        if entry.get("status", "posted") not in _POSTED_STATUSES:
            continue

        folder = entry.get("folder", "")
        if folder:
            slug = _folder_slug(folder)
            if slug:
                slugs.add(slug)

        img_hash = entry.get("image_sha256")
        if not img_hash and folder:
            fp = Path(folder)
            if fp.is_dir():
                img_hash = _image_sha256(fp / "image.jpg")
        if img_hash:
            image_hashes.add(img_hash)

        src_url = entry.get("source_url")
        if not src_url and folder:
            fp = Path(folder)
            if fp.is_dir():
                src_url = _read_source_url(fp)
        if src_url:
            source_urls.add(src_url)

        txt_hash = entry.get("post_text_sha256")
        if not txt_hash and folder:
            post_txt = Path(folder) / "post.txt"
            if post_txt.is_file():
                try:
                    txt = post_txt.read_text(encoding="utf-8").strip()
                    txt_hash = hashlib.sha256(_normalize_text(txt).encode()).hexdigest()
                except Exception:
                    pass
        if txt_hash:
            text_hashes.add(txt_hash)

    return {
        "slugs": slugs,
        "image_hashes": image_hashes,
        "source_urls": source_urls,
        "text_hashes": text_hashes,
    }


def _check_duplicate(candidate_folder: Path, bundle: dict, signals: dict) -> str | None:
    """Return a human-readable reason if this candidate is a duplicate, else None."""
    slug = _folder_slug(candidate_folder)
    if slug in signals["slugs"]:
        return f"folder slug already posted: {slug}"

    img_hash = _image_sha256(bundle["image_path"])
    if img_hash and img_hash in signals["image_hashes"]:
        return f"image already posted (sha256={img_hash[:12]}...)"

    src_url = _read_source_url(candidate_folder)
    if src_url and src_url in signals["source_urls"]:
        return f"source URL already posted: {src_url[:60]}"

    norm_hash = hashlib.sha256(_normalize_text(bundle["text"]).encode()).hexdigest()
    if norm_hash in signals["text_hashes"]:
        return f"post text already posted (hash={norm_hash[:12]}...)"

    return None


# ---------------------------------------------------------------------------
# Post text repair
# ---------------------------------------------------------------------------

def _post_text_from_parts(title: str, caption: str, source_url: str) -> str:
    """Assemble and truncate post text from components, same logic as select_best_content.py."""
    def _assemble(cap: str, include_hashtags: bool = True) -> str:
        parts = [title.strip()]
        if cap:
            parts += ["", cap.strip()]
        parts += ["", "—", f"Source: {source_url}"]
        if include_hashtags:
            parts.append(HASHTAGS)
        return "\n".join(parts)

    if len(_assemble(caption)) <= MAX_POST_CHARS:
        return _assemble(caption) + "\n"

    words = caption.strip().split()
    kept: list[str] = []
    for word in words:
        test_cap = " ".join(kept + [word]) + "…"
        if len(_assemble(test_cap)) <= MAX_POST_CHARS:
            kept.append(word)
        else:
            break
    if kept:
        return _assemble(" ".join(kept) + "…") + "\n"

    if len(_assemble("")) <= MAX_POST_CHARS:
        return _assemble("") + "\n"

    return _assemble("", include_hashtags=False)[:MAX_POST_CHARS].strip() + "\n"


def _repair_post_text(folder: Path, current_text: str) -> str | None:
    """Produce a repaired post body that fits within MAX_POST_CHARS.

    Returns the repaired text (stripped, no trailing newline) or None if
    repair is not possible. Tries metadata-aware reconstruction first, then
    falls back to word-by-word truncation of the existing text.
    """
    meta_path = folder / "metadata.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            title = (meta.get("title") or "").strip()
            caption = (meta.get("caption") or "").strip()
            source_url = (meta.get("source_url") or "").strip()
            if title and source_url:
                repaired = _post_text_from_parts(title, caption, source_url).strip()
                if len(repaired) <= MAX_POST_CHARS:
                    return repaired
        except Exception:
            pass

    # Fallback: word-by-word truncation of the existing text.
    words = current_text.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word])
        if len(candidate) + 1 <= MAX_POST_CHARS:  # +1 for ellipsis
            kept.append(word)
        else:
            break
    if kept and len(" ".join(kept)) < len(current_text):
        return " ".join(kept) + "…"

    return None


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

    # Entries that count toward the daily cap (real posts only).
    posted_today = [
        e for e in log
        if e.get("date") == date_str and e.get("status", "posted") in _POSTED_STATUSES
    ]
    # Ranks we must not retry (posted + skipped).
    done_ranks_today = {
        e["rank"] for e in log
        if e.get("date") == date_str and e.get("status", "posted") in _DONE_STATUSES
    }

    rank_summary = sorted(done_ranks_today) if done_ranks_today else "none"
    print(f"[queue] posted_today={len(posted_today)}/{max_posts}  done_ranks={rank_summary}")

    if len(posted_today) >= max_posts:
        print(f"[queue] max posts ({max_posts}) already reached for {date_str}. Nothing to do.")
        return

    # Enforce minimum spacing against the most recent actually-posted entry.
    if min_minutes > 0:
        all_timestamps = [
            e["posted_at"] for e in log
            if e.get("posted_at") and e.get("status", "posted") in _POSTED_STATUSES
        ]
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

    eligible = [(rank, folder) for rank, folder in rank_folders if rank not in done_ranks_today]
    if not eligible:
        print(f"[queue] all available ranks already posted/skipped for {date_str}.")
        return

    next_rank, next_folder = eligible[0]
    print(f"[queue] next eligible rank={next_rank}  folder={next_folder.name}")

    bundle = _read_post_bundle(date_str, next_rank)

    # Build duplicate-detection index from the full log (runs once, reads from disk).
    dup_signals = _build_dup_signals(log)

    # --- Duplicate check ---
    dup_reason = _check_duplicate(next_folder, bundle, dup_signals)
    if dup_reason:
        print(f"[queue] skipped duplicate rank={next_rank}  reason={dup_reason}")
        if send:
            log.append({
                "date": date_str,
                "rank": next_rank,
                "folder": str(next_folder),
                "status": "skipped_duplicate",
                "reason": dup_reason,
                "skipped_at": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            _save_log(log)
        else:
            print("[queue] dry run — skip not recorded to log.")
        return

    # --- 280-char check and repair ---
    char_count = len(bundle["text"])
    if char_count > MAX_POST_CHARS:
        repaired = _repair_post_text(next_folder, bundle["text"])
        if repaired and len(repaired) <= MAX_POST_CHARS:
            print(f"[queue] repaired post length rank={next_rank}  was={char_count} now={len(repaired)}")
            (next_folder / "post.txt").write_text(repaired + "\n", encoding="utf-8")
            bundle["text"] = repaired
            char_count = len(repaired)
        else:
            print(f"[queue] skipped over 280 rank={next_rank}  chars={char_count}")
            if send:
                log.append({
                    "date": date_str,
                    "rank": next_rank,
                    "folder": str(next_folder),
                    "status": "skipped_over_280",
                    "reason": f"post text is {char_count} chars (limit {MAX_POST_CHARS}), repair not possible",
                    "skipped_at": datetime.datetime.now().isoformat(timespec="seconds"),
                })
                _save_log(log)
            else:
                print("[queue] dry run — skip not recorded to log.")
            return

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
        # Should not be reachable after the repair block above, but guard anyway.
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
        "status": "posted",
        "image_sha256": _image_sha256(bundle["image_path"]),
        "post_text_sha256": hashlib.sha256(_normalize_text(bundle["text"]).encode()).hexdigest(),
        "source_url": _read_source_url(next_folder),
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
