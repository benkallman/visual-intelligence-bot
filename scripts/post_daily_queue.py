#!/usr/bin/env python3
"""
Daily X posting queue: post the next eligible ranked export for a given date.

Wraps post_to_x.py — reuses its OAuth, media upload, and tweet creation logic
without duplicating it — and adds a persistent log that tracks what has been
posted and enforces minimum spacing between posts.

Behavior:
- Discovers all rank folders under exports/social/<date>/ in rank order.
- If none exist and --auto-build-pack is set, builds them by calling
  export_pack_candidates and export_pack_social --copy-to-social.
- Skips ranks already logged in data/social_post_log.json for that date
  (including ranks marked skipped_duplicate or skipped_over_280).
- Stops cleanly if the daily max-posts cap is already reached.
- Waits (prints next-allowed time and exits 0) if not enough time has elapsed
  since the last post across any date.
- Scans eligible ranks in order; for each one:
    - Checks for duplicate content (folder slug, image hash, source URL,
      post text) against the full log.
    - If duplicate: logs the skip (in --send mode) and continues to the
      next rank in the same invocation.
    - If post.txt exceeds 280 chars, repairs it in-place using metadata.json
      then continues; if repair fails, logs the skip and continues.
    - On the first postable rank: previews it (or posts it with --send).
- Posts only the single next eligible rank per invocation.
- Dry-run by default; --send is required to post to X.
- On a successful send, appends one entry to data/social_post_log.json.

The script is designed to be called in a polling loop:

    while ($true) {
        python scripts/post_daily_queue.py --date today --max-posts 3 `
            --min-minutes-between-posts 180 `
            --auto-build-pack japanese_wood_historical --auto-build-top 3 `
            --send
        Start-Sleep -Seconds 1800
    }

Each invocation either posts one item or exits 0 with a readable reason.

Usage:
    python scripts/post_daily_queue.py --date today
    python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180
    python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180 --send
    python scripts/post_daily_queue.py --date today --max-posts 3 --min-minutes-between-posts 180 \\
        --auto-build-pack japanese_wood_historical --auto-build-top 3 --send
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
    _MAX_UPLOAD_BYTES,
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
# Auto-build from source pack
# ---------------------------------------------------------------------------

def _auto_build(pack_id: str, date_str: str, top: int) -> None:
    """Build exports/social/<date>/ from a source pack by calling the export pipeline."""
    try:
        import export_pack_candidates as _epc
        import export_pack_social as _eps
    except ImportError as exc:
        print(f"[queue] auto-build: cannot import pack scripts: {exc}", file=sys.stderr)
        return

    print(f"[queue] auto-build: export_pack_candidates pack={pack_id!r} date={date_str}")
    try:
        _epc.main(pack_id=pack_id, date_str=date_str)
    except Exception as exc:
        print(f"[queue] auto-build: export_pack_candidates error (continuing): {exc}")

    print(f"[queue] auto-build: export_pack_social pack={pack_id!r} date={date_str} top={top} --copy-to-social")
    try:
        _eps.main(
            pack_id=pack_id,
            date_str=date_str,
            top=top,
            dry_run=False,
            copy_to_social=True,
            force=False,
        )
    except Exception as exc:
        print(f"[queue] auto-build: export_pack_social error: {exc}")


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


def _read_pack_id(folder: Path) -> str | None:
    """Read pack_id from a queue folder's metadata.json."""
    meta = folder / "metadata.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return data.get("pack_id") or None
    except Exception:
        return None


def _entry_pack_id(entry: dict) -> str | None:
    """Return pack_id for a log entry: stored field first, metadata.json fallback."""
    pack_id = entry.get("pack_id")
    if pack_id:
        return pack_id
    folder = entry.get("folder", "")
    if folder:
        return _read_pack_id(Path(folder))
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

def main(
    date_value: str,
    max_posts: int,
    min_minutes: int,
    send: bool,
    auto_build_pack: str | None = None,
    auto_build_top: int = 3,
    rank: int | None = None,
    skip_cooldown: bool = False,
    pack_filter: str | None = None,
) -> None:
    """Scan the queue and post (or preview) the next eligible rank.

    Skips duplicate and over-limit ranks within one invocation rather than
    stopping at the first problem. Posts at most one item per call.

    When --rank is given, targets that rank directly and skips the daily cap
    and (if --skip-cooldown) the spacing check. The post is still duplicate-
    checked and logged normally.

    When --pack is given, only folders whose metadata.json has pack_id matching
    that value are considered. posted_today and done_ranks_today are scoped to
    that pack; max_posts applies per-pack. Cooldown and duplicate detection
    remain global.

    Exits 0 in all expected non-error states (wait, cap reached, nothing left).
    Exits 1 only on missing credentials or an API error.
    """
    date_str = _resolve_date(date_value)
    print(f"[queue] date={date_str}")
    if pack_filter:
        print(f"[queue] pack={pack_filter}")

    log = _load_log()

    if pack_filter:
        # Scope posted/done counts to this pack only.
        posted_today = [
            e for e in log
            if e.get("date") == date_str
            and e.get("status", "posted") in _POSTED_STATUSES
            and _entry_pack_id(e) == pack_filter
        ]
        done_ranks_today: set[int] = {
            e["rank"] for e in log
            if e.get("date") == date_str
            and e.get("status", "posted") in _DONE_STATUSES
            and _entry_pack_id(e) == pack_filter
        }
    else:
        # Global (all-pack) behaviour — backward-compatible default.
        posted_today = [
            e for e in log
            if e.get("date") == date_str and e.get("status", "posted") in _POSTED_STATUSES
        ]
        done_ranks_today = {
            e["rank"] for e in log
            if e.get("date") == date_str and e.get("status", "posted") in _DONE_STATUSES
        }

    rank_summary = sorted(done_ranks_today) if done_ranks_today else "none"
    if pack_filter:
        print(f"[queue] posted_today_for_pack={len(posted_today)}/{max_posts}  done_ranks={rank_summary}")
    else:
        print(f"[queue] posted_today={len(posted_today)}/{max_posts}  done_ranks={rank_summary}")

    # --rank bypasses the daily cap and optionally the cooldown.
    if rank is None:
        if len(posted_today) >= max_posts:
            print(f"[queue] max posts ({max_posts}) already reached for {date_str}. Nothing to do.")
            return

    # Enforce minimum spacing against the most recent actually-posted entry.
    if not skip_cooldown and min_minutes > 0:
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
    if pack_filter:
        rank_folders = [(r, f) for r, f in rank_folders if _read_pack_id(f) == pack_filter]

    if not rank_folders and auto_build_pack:
        print(
            f"[queue] no exports for {date_str} — "
            f"auto-building from pack {auto_build_pack!r} (top {auto_build_top})"
        )
        _auto_build(auto_build_pack, date_str, auto_build_top)
        rank_folders = _discover_rank_folders(date_str)

    if not rank_folders:
        print(f"[queue] no export folders found under exports/social/{date_str}/")
        if auto_build_pack:
            print(
                f"[queue] auto-build produced no rank folders — "
                f"check that pack {auto_build_pack!r} has candidates with raster images"
            )
        else:
            print(f"[queue] run: python scripts/select_best_content.py --top {max_posts} --date {date_str}")
            print(f"[queue] or pass --auto-build-pack <pack_id> to build automatically")
        return

    if rank is not None:
        # Targeted post: select only the requested rank, ignoring done_ranks_today.
        eligible = [(r, f) for r, f in rank_folders if r == rank]
        if not eligible:
            print(f"[queue] rank={rank} not found under exports/social/{date_str}/")
            return
        print(f"[queue] targeted rank={rank} (--rank overrides cap and done-ranks filter)")
    else:
        eligible = [(r, f) for r, f in rank_folders if r not in done_ranks_today]
        if not eligible:
            print(f"[queue] all available ranks already posted/skipped for {date_str}.")
            return

    # Build duplicate-detection index once from the full log.
    dup_signals = _build_dup_signals(log)

    # Scan eligible ranks in order, skipping problems, until we find one to post.
    for rank, folder in eligible:

        try:
            bundle = _read_post_bundle(date_str, rank)
        except FileNotFoundError as exc:
            print(f"[queue] skip rank={rank}: {exc}")
            continue

        # --- Duplicate check ---
        dup_reason = _check_duplicate(folder, bundle, dup_signals)
        if dup_reason:
            print(f"[queue] skipped duplicate rank={rank}  folder={folder.name}  reason={dup_reason}")
            if send:
                log.append({
                    "date": date_str,
                    "rank": rank,
                    "folder": str(folder),
                    "pack_id": _read_pack_id(folder),
                    "status": "skipped_duplicate",
                    "reason": dup_reason,
                    "skipped_at": datetime.datetime.now().isoformat(timespec="seconds"),
                })
                _save_log(log)
            else:
                print("[queue] dry run: next run with --send will record this skip and advance")
            continue

        # --- 280-char check and repair ---
        char_count = len(bundle["text"])
        if char_count > MAX_POST_CHARS:
            repaired = _repair_post_text(folder, bundle["text"])
            if repaired and len(repaired) <= MAX_POST_CHARS:
                print(f"[queue] repaired post length rank={rank}  was={char_count} now={len(repaired)}")
                (folder / "post.txt").write_text(repaired + "\n", encoding="utf-8")
                bundle["text"] = repaired
                char_count = len(repaired)
            else:
                print(f"[queue] skipped over 280 rank={rank}  chars={char_count}")
                if send:
                    log.append({
                        "date": date_str,
                        "rank": rank,
                        "folder": str(folder),
                        "pack_id": _read_pack_id(folder),
                        "status": "skipped_over_280",
                        "reason": (
                            f"post text is {char_count} chars "
                            f"(limit {MAX_POST_CHARS}), repair not possible"
                        ),
                        "skipped_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    })
                    _save_log(log)
                else:
                    print("[queue] dry run: next run with --send will record this skip and advance")
                continue

        # --- This rank is postable ---
        print(f"[queue] next eligible rank={rank}  folder={folder.name}")
        limit_note = f"[within {MAX_POST_CHARS}]"
        print(f"[queue] characters={char_count} {limit_note}")
        print("[queue] preview:")
        print()
        print(bundle["text"])
        print()

        if not send:
            print("[queue] dry run only. Use --send to post to X.")
            return

        # Credentials are loaded only in send mode so dry runs need no env vars.
        resolved = load_social_env(str(ROOT_DIR))
        missing = [key for key in SEND_REQUIRED_VARS if not resolved[key]["present"]]
        if missing:
            print(f"[queue] Error: missing credentials: {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)

        credentials = {key: str(resolved[key]["value"]) for key in SEND_REQUIRED_VARS}
        try:
            response = _send_post(bundle, credentials)
        except RuntimeError as exc:
            reason = str(exc)
            print(f"[queue] post failed: {reason}")
            fail_entry = {
                "date": date_str,
                "rank": rank,
                "folder": str(folder),
                "pack_id": _read_pack_id(folder),
                "failed_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "status": "post_failed",
                "reason": reason[:500],
            }
            log.append(fail_entry)
            _save_log(log)
            # Log to oversize doc if the failure looks like a size/upload error.
            try:
                orig_size = bundle["image_path"].stat().st_size
                is_size_related = (
                    orig_size > _MAX_UPLOAD_BYTES
                    or "too large" in reason.lower()
                    or "15728640" in reason
                    or "14680064" in reason
                )
                if is_size_related:
                    from src.integrations.google_drive_log import log_oversized_image as _log_oversize
                    _log_oversize(
                        entry=fail_entry,
                        folder=folder,
                        image_path=str(bundle["image_path"]),
                        error_text=reason[:500],
                    )
            except Exception as _ov_exc:
                print(f"[oversize-log] unexpected error: {_ov_exc}")
            return

        tweet_id = None
        if isinstance(response, dict):
            tweet_id = (response.get("data") or {}).get("id")

        entry = {
            "date": date_str,
            "rank": rank,
            "folder": str(folder),
            "pack_id": _read_pack_id(folder),
            "tweet_id": tweet_id,
            "posted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "text_preview": bundle["text"][:TEXT_PREVIEW_MAX],
            "status": "posted",
            "image_sha256": _image_sha256(bundle["image_path"]),
            "post_text_sha256": hashlib.sha256(_normalize_text(bundle["text"]).encode()).hexdigest(),
            "source_url": _read_source_url(folder),
        }
        log.append(entry)
        _save_log(log)

        try:
            from src.integrations.google_drive_log import log_post as _drive_log_post
            _drive_log_post(entry, folder, bundle["image_path"], bundle["text"])
        except Exception as _drive_exc:
            print(f"[drive-log] unexpected error: {_drive_exc}")

        media_info = response.get("media_info", {})
        if media_info.get("was_normalized"):
            try:
                from src.integrations.google_drive_log import log_oversized_image as _log_oversize
                tweet_url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None
                _log_oversize(
                    entry=entry,
                    folder=folder,
                    image_path=str(media_info.get("original_path", bundle["image_path"])),
                    error_text="image normalized before upload",
                    normalized_path=str(media_info.get("upload_path", "")),
                    normalized_size=media_info.get("upload_size"),
                    posted_later_url=tweet_url,
                    original_dims=media_info.get("original_dims"),
                )
            except Exception as _ov_exc:
                print(f"[oversize-log] unexpected error: {_ov_exc}")

        print(f"[queue] posted tweet_id={tweet_id}")
        print(f"[queue] log updated: {LOG_PATH}")
        return  # One post per invocation.

    # All eligible ranks were skipped.
    print(f"[queue] no eligible ranks remaining for {date_str} (all posted, skipped, or exhausted).")


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
    parser.add_argument(
        "--auto-build-pack", default=None,
        metavar="PACK_ID",
        help=(
            "Source pack ID to auto-build from when no social exports exist "
            "(e.g. japanese_wood_historical)"
        ),
    )
    parser.add_argument(
        "--auto-build-top", type=int, default=3,
        metavar="N",
        help="Number of items to build when auto-building (default: 3)",
    )
    parser.add_argument(
        "--rank", type=int, default=None,
        metavar="N",
        help=(
            "Target a specific rank directly, bypassing the daily cap and "
            "done-ranks filter. Duplicate detection and logging still apply. "
            "Combine with --skip-cooldown to also bypass spacing."
        ),
    )
    parser.add_argument(
        "--skip-cooldown", action="store_true",
        help="Skip the min-minutes-between-posts spacing check.",
    )
    parser.add_argument(
        "--pack", default=None,
        metavar="PACK_ID",
        help=(
            "Restrict to a specific source pack. Only queue folders whose "
            "metadata.json has pack_id matching PACK_ID are considered. "
            "posted_today and done_ranks counts are scoped to this pack; "
            "--max-posts applies per-pack. Cooldown and duplicate detection "
            "remain global."
        ),
    )
    args = parser.parse_args()
    try:
        main(
            date_value=args.date,
            max_posts=args.max_posts,
            min_minutes=args.min_minutes_between_posts,
            send=args.send,
            auto_build_pack=args.auto_build_pack,
            auto_build_top=args.auto_build_top,
            rank=args.rank,
            skip_cooldown=args.skip_cooldown,
            pack_filter=args.pack,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[queue] Error: {exc}", file=sys.stderr)
        sys.exit(1)
