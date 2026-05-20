#!/usr/bin/env python3
"""
Schedule future social posts from a source-pack candidate pool.

Fills exports/social/<YYYY-MM-DD>/ folders for each day from --from-date+1
through --from-date+days, adding up to --per-day items per date (total per
folder capped at --daily-post-cap). Dry-run by default; use --write to
download images and create the queue folders.

Exclusion logic — a candidate is skipped when its slug or source URL appears in:
  - data/social_post_log.json (all statuses)
  - any existing exports/social/*/ folder (all dates, all ranks)
  - exports/social-packs/<pack>/*/ staging folders

Each candidate in the ranked pool is used at most once across all scheduled
dates so the same image cannot appear on two future days.

Rate limiting:
  --download-delay-seconds (default 5) sleeps between each image download.
  --max-downloads-per-run  (default 25) stops after N successful downloads.
  --retry-after-429-seconds (default 900) is printed as a cooldown suggestion
    when Wikimedia returns HTTP 429; the run stops cleanly and exits 0.
  Already-written folders are never removed on 429; only the in-progress
  folder for the failing download is cleaned up.

Writes a manifest to:
  exports/social-schedule/<pack>/<from-date>/schedule.json
  exports/social-schedule/<pack>/<from-date>/schedule.md

Usage:
  python scripts/schedule_pack_future_posts.py --pack japanese_wood_historical \\
      --from-date today --days 7 --per-day 20
  python scripts/schedule_pack_future_posts.py --pack japanese_wood_historical \\
      --from-date today --days 7 --per-day 20 --write
  python scripts/schedule_pack_future_posts.py --pack japanese_wood_historical \\
      --from-date today --days 7 --per-day 20 --write \\
      --download-delay-seconds 5 --max-downloads-per-run 25
  python scripts/schedule_pack_future_posts.py --pack tibetan_mystical_traditions_historical \\
      --from-date today --days 1 --include-today --per-day 5 --write
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPTS_DIR)

from export_pack_social import (
    SOCIAL_PACKS_DIR,
    SOCIAL_QUEUE_DIR,
    _clean_text,
    _display_title,
    _item_slug,
    _is_raster_url,
    _load_from_disk,
    _load_from_export,
    _make_caption,
    _sort_key,
)

ROOT_DIR = Path(_SCRIPTS_DIR).parent
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"
SCHEDULE_DIR = ROOT_DIR / "exports" / "social-schedule"

_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}


# ---------------------------------------------------------------------------
# Rate-limit sentinel
# ---------------------------------------------------------------------------

class _RateLimitError(Exception):
    """Raised when Wikimedia returns HTTP 429 Too Many Requests."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_log() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _build_posted_signals(log: list[dict]) -> dict[str, set[str]]:
    image_keys: set[str] = set()
    source_urls: set[str] = set()
    text_hashes: set[str] = set()
    slugs: set[str] = set()

    for entry in log:
        if entry.get("status") not in {"posted", "posted_manual_backfill"}:
            continue
        folder = entry.get("folder") or ""
        if folder:
            slug = re.sub(r"^\d+-", "", Path(folder).name)
            if slug:
                slugs.add(slug)
        src_url = str(entry.get("source_url") or "").strip()
        if src_url:
            source_urls.add(src_url)
            image_keys.add(src_url)
        image_hash = str(entry.get("image_sha256") or "").strip()
        if image_hash:
            image_keys.add(image_hash)
        text_hash = str(entry.get("post_text_sha256") or "").strip()
        if text_hash:
            text_hashes.add(text_hash)

    return {
        "image_keys": image_keys,
        "source_urls": source_urls,
        "text_hashes": text_hashes,
        "slugs": slugs,
    }


def _read_queue_metadata(folder: Path) -> dict:
    meta_path = folder / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _repair_queue_image(folder: Path, delay_seconds: float = 0) -> bool:
    image_path = folder / "image.jpg"
    if image_path.is_file():
        return True

    meta = _read_queue_metadata(folder)
    image_url = (meta.get("image_url") or "").strip()
    if not image_url:
        return False

    try:
        _download_image_scheduled(image_url, image_path, delay_seconds)
        print(f"[schedule] repaired missing image: {folder.name}")
        return True
    except Exception as exc:
        print(f"[schedule] could not repair image for {folder.name}: {exc}")
        try:
            if image_path.exists():
                image_path.unlink()
        except OSError:
            pass
        return False


def _folder_is_valid(folder: Path, repair_missing: bool = False, download_delay: float = 0) -> bool:
    if not (folder / "metadata.json").is_file() or not (folder / "post.txt").is_file():
        return False
    if (folder / "image.jpg").is_file():
        return True
    if repair_missing:
        return _repair_queue_image(folder, delay_seconds=download_delay)
    return False


def _build_exclusion_sets(pack_id: str) -> tuple[set[str], set[str]]:
    """Return (excluded_slugs, excluded_source_urls) covering log + queue + staging."""
    excluded_slugs: set[str] = set()
    excluded_urls: set[str] = set()

    for entry in _load_log():
        folder = entry.get("folder", "")
        if folder:
            slug = re.sub(r"^\d{2}-", "", Path(folder).name)
            if slug:
                excluded_slugs.add(slug)
        url = entry.get("source_url") or ""
        if url:
            excluded_urls.add(url)

    if SOCIAL_QUEUE_DIR.is_dir():
        for date_dir in SOCIAL_QUEUE_DIR.iterdir():
            if not date_dir.is_dir():
                continue
            for folder in date_dir.iterdir():
                if not folder.is_dir():
                    continue
                if not _folder_is_valid(folder):
                    continue
                m = re.match(r"^\d+-(.+)$", folder.name)
                if m:
                    excluded_slugs.add(m.group(1))
                meta = _read_queue_metadata(folder)
                src_url = (meta.get("source_url") or meta.get("page_url") or "").strip()
                if src_url:
                    excluded_urls.add(src_url)

    staging_root = SOCIAL_PACKS_DIR / pack_id
    if staging_root.is_dir():
        for date_dir in staging_root.iterdir():
            if not date_dir.is_dir():
                continue
            for folder in date_dir.iterdir():
                if not folder.is_dir():
                    continue
                m = re.match(r"^\d+-(.+)$", folder.name)
                if m:
                    excluded_slugs.add(m.group(1))

    return excluded_slugs, excluded_urls


def _existing_rank_folders(date_str: str) -> list[tuple[int, Path]]:
    base = SOCIAL_QUEUE_DIR / date_str
    if not base.is_dir():
        return []
    result = []
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        if not _folder_is_valid(folder):
            continue
        m = re.match(r"^(\d+)-", folder.name)
        if m:
            result.append((int(m.group(1)), folder))
    return sorted(result)


def _existing_pack_count(date_str: str, pack_id: str) -> int:
    """Count queue folders on date_str whose metadata.json has pack_id == pack_id."""
    base = SOCIAL_QUEUE_DIR / date_str
    if not base.is_dir():
        return 0
    count = 0
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        if not _folder_is_valid(folder):
            continue
        meta_path = folder / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("pack_id") == pack_id:
                count += 1
        except Exception:
            pass
    return count


def _item_is_excluded(
    item: dict,
    excluded_slugs: set[str],
    excluded_urls: set[str],
) -> str | None:
    candidate_slug = _item_slug(item)
    if candidate_slug in excluded_slugs:
        return f"slug already in use: {candidate_slug}"
    src_url = item.get("source_url") or item.get("page_url") or ""
    if src_url and src_url in excluded_urls:
        return f"source URL already in use: {src_url[:60]}"
    return None


def _download_image_scheduled(url: str, dest: Path, delay_seconds: float) -> None:
    """Download url to dest, sleeping delay_seconds first.

    Raises _RateLimitError on HTTP 429 (Wikimedia rate limit).
    Raises RuntimeError on any other network or content failure.
    Never swallows exceptions — the caller decides cleanup and control flow.
    """
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=60)
    except Exception as exc:
        raise RuntimeError(f"network error: {exc}") from exc

    if resp.status_code == 429:
        raise _RateLimitError(f"HTTP 429 from Wikimedia ({url[:60]})")

    try:
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"HTTP {resp.status_code}: {exc}") from exc

    ct = resp.headers.get("content-type", "")
    if "text/" in ct or "html" in ct:
        raise RuntimeError(f"URL returned HTML, not an image ({url[:60]})")

    dest.write_bytes(resp.content)


def _write_item(
    date_str: str,
    rank: int,
    item: dict,
    caption: str,
    pack_id: str,
    download_delay: float = 0,
) -> Path | None:
    """Write post.txt, metadata.json, and image.jpg for one scheduled item.

    Returns the created folder path on success.
    Returns None when the folder already exists or on a non-rate-limit download
    failure (folder is cleaned up before returning).
    Raises _RateLimitError when Wikimedia returns 429 (incomplete folder is
    cleaned up before re-raising so the caller sees a consistent state).
    """
    title = _display_title(item)
    image_url = item.get("direct_image_url") or ""
    year = item.get("date_year")
    license_text = _clean_text(item.get("license") or "unknown")
    page_url = item.get("source_url") or item.get("page_url") or ""
    slug_str = _item_slug(item)
    folder_name = f"{rank:02d}-{slug_str}"
    folder = SOCIAL_QUEUE_DIR / date_str / folder_name

    if folder.exists():
        print(f"  [skip] folder already exists: {folder_name}")
        return None

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "post.txt").write_text(caption, encoding="utf-8")

    meta = {
        "rank": rank,
        "pack_id": pack_id,
        "candidate_id": item.get("candidate_id"),
        "title": title,
        "artist": _clean_text(item.get("artist") or ""),
        "year": year,
        "license": license_text,
        "image_url": image_url,
        "source_url": page_url,
        "page_url": page_url,
        "caption": caption,
        "caption_chars": len(caption),
        "scheduled_for": date_str,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        _download_image_scheduled(image_url, folder / "image.jpg", download_delay)
    except _RateLimitError:
        shutil.rmtree(folder, ignore_errors=True)
        raise  # propagate so the caller can stop the write loop
    except RuntimeError as exc:
        print(f"  [warn] image download failed for rank={rank}: {exc}")
        shutil.rmtree(folder, ignore_errors=True)
        return None

    return folder


def _write_manifest(
    pack_id: str,
    from_date_str: str,
    schedule_days: list[dict],
    write_mode: bool,
    per_day: int,
    daily_post_cap: int,
    pack_daily_cap: int,
    stopped_due_to_rate_limit: bool = False,
) -> None:
    out_dir = SCHEDULE_DIR / pack_id / from_date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "pack_id": pack_id,
        "from_date": from_date_str,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "write_mode": write_mode,
        "scheduled_per_day_target": per_day,
        "daily_post_cap": daily_post_cap,
        "pack_daily_cap": pack_daily_cap,
        "stopped_due_to_rate_limit": stopped_due_to_rate_limit,
        "days": schedule_days,
    }
    json_path = out_dir / "schedule.json"
    json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Social Schedule: {pack_id}",
        f"",
        f"**From date:** {from_date_str}  ",
        f"**Generated:** {manifest['generated_at']}  ",
        f"**Mode:** {'write' if write_mode else 'dry-run'}  ",
        f"**Scheduled per-day target:** {per_day}  ",
        f"**Pack daily cap (per-pack quota):** {pack_daily_cap}  ",
        f"**Global daily post cap (post_daily_queue.py):** {daily_post_cap}  ",
        f"**Stopped due to rate limit:** {stopped_due_to_rate_limit}",
        f"",
    ]
    for day in schedule_days:
        lines.append(f"## {day['date']}")
        lines.append(f"")
        lines.append(
            f"- Existing (this pack): {day.get('existing_for_pack', day.get('existing_count', 0))}  "
            f"Added: {day['added']}  "
            f"Skipped duplicate: {day['skipped_duplicate']}"
        )
        lines.append(f"")
        for it in day.get("items", []):
            lines.append(f"### Rank {it['rank']}: {it['title'][:70]}")
            lines.append(f"")
            lines.append(f"- **Caption chars:** {it['caption_chars']}")
            if it.get("source_url"):
                lines.append(f"- **Source:** {it['source_url']}")
            lines.append(f"- **Caption:** {it['caption']}")
            lines.append(f"")

    md_path = out_dir / "schedule.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"[schedule] manifest written: {json_path}")
    print(f"[schedule] manifest written: {md_path}")


def _rebuild_pack_export(pack_id: str, date_str: str) -> None:
    try:
        import export_pack_candidates as _epc
    except ImportError as exc:
        print(f"[schedule] could not import export_pack_candidates: {exc}")
        return

    print(f"[schedule] rebuilding candidate export for pack={pack_id} date={date_str}")
    try:
        _epc.main(pack_id=pack_id, date_str=date_str)
    except Exception as exc:
        print(f"[schedule] export rebuild failed: {exc}")


def _refresh_pack_candidates(pack_id: str, date_str: str, refresh_max_total: int) -> None:
    try:
        import run_source_pack as _rsp
    except ImportError as exc:
        print(f"[schedule] could not import run_source_pack: {exc}")
        return

    print(
        f"[schedule] refreshing candidate pool for pack={pack_id} "
        f"(discover-only max_total={refresh_max_total})"
    )
    try:
        _rsp.main(
            pack_id=pack_id,
            max_total=refresh_max_total,
            per_query_limit=max(20, min(50, refresh_max_total)),
            dry_run=False,
            discover_only=True,
            ingest_only=False,
            limit_ingest=None,
        )
    except Exception as exc:
        print(f"[schedule] candidate refresh failed: {exc}")
        return

    _rebuild_pack_export(pack_id, date_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    pack_id: str,
    from_date_str: str,
    days: int,
    per_day: int,
    daily_post_cap: int,
    pack_daily_cap: int,
    write: bool,
    download_delay: float,
    max_downloads: int,
    retry_after_429: int,
    include_today: bool = False,
    refresh_if_empty: bool = False,
    refresh_max_total: int = 40,
) -> None:
    sys.stdout.reconfigure(errors="replace")

    from_date = datetime.date.fromisoformat(from_date_str)
    log = _load_log()
    posted_history = _build_posted_signals(log)
    mode_label = "write" if write else "dry-run"
    print(
        f"[schedule] pack={pack_id}  from_date={from_date_str}  days={days}  "
        f"per_day={per_day}  pack_daily_cap={pack_daily_cap}  mode={mode_label}"
    )
    if write:
        print(
            f"[schedule] download_delay={download_delay}s  "
            f"max_downloads_per_run={max_downloads}  "
            f"retry_after_429={retry_after_429}s"
        )
    print()

    # Load candidates from the pack export for from_date
    items = _load_from_export(pack_id, from_date_str)
    if items is None:
        _rebuild_pack_export(pack_id, from_date_str)
        items = _load_from_export(pack_id, from_date_str)
    if items is not None:
        src = f"exports/source-packs/{pack_id}/{from_date_str}/candidates.json"
        print(f"[schedule] loaded {len(items)} candidates from {src}")
    else:
        items = _load_from_disk(pack_id)
        print(f"[schedule] loaded {len(items)} candidates from disk (fallback)")

    if not items:
        if refresh_if_empty:
            _refresh_pack_candidates(pack_id, from_date_str, refresh_max_total)
            items = _load_from_export(pack_id, from_date_str) or _load_from_disk(pack_id)
        if not items:
            print(f"[schedule] no candidates found for pack={pack_id!r}")
            print(f"[schedule] run: python scripts/export_pack_candidates.py --pack {pack_id} --date {from_date_str}")
            print(
                f"[schedule] or refresh candidates: python scripts/run_source_pack.py "
                f"--pack {pack_id} --max-total {refresh_max_total} --discover-only"
            )
            return

    raster = [it for it in items if _is_raster_url(it.get("direct_image_url") or "")]
    n_non_raster = len(items) - len(raster)
    print(f"[schedule] {len(raster)} raster-image candidates ({n_non_raster} skipped -- no image URL or non-raster)")

    excluded_slugs, excluded_urls = _build_exclusion_sets(pack_id)
    print(
        f"[schedule] exclusion set: {len(excluded_slugs)} slugs  {len(excluded_urls)} source URLs  "
        f"(log + all queue folders + staging)"
    )

    fresh: list[dict] = []
    n_excluded = 0
    for item in raster:
        if _item_is_excluded(item, excluded_slugs, excluded_urls):
            n_excluded += 1
        else:
            fresh.append(item)

    print(f"[schedule] fresh candidates after exclusion: {len(fresh)} ({n_excluded} excluded)")
    print()

    if not fresh:
        if refresh_if_empty:
            _refresh_pack_candidates(pack_id, from_date_str, refresh_max_total)
            items = _load_from_export(pack_id, from_date_str) or _load_from_disk(pack_id)
            raster = [it for it in items if _is_raster_url(it.get("direct_image_url") or "")]
            excluded_slugs, excluded_urls = _build_exclusion_sets(pack_id)
            fresh = []
            for item in raster:
                if not _item_is_excluded(item, excluded_slugs, excluded_urls):
                    fresh.append(item)
            print(f"[schedule] fresh candidates after refresh: {len(fresh)}")
            print()
        if not fresh:
            print("[schedule] no fresh candidates to schedule.")
            return

    ranked_pool = sorted(fresh, key=_sort_key, reverse=True)

    # stop_reason tracks why we exited early: None | "rate_limited" | "download_cap"
    stop_reason: str | None = None
    download_count = 0
    pool_idx = 0
    schedule_days: list[dict] = []

    start_offset = 0 if include_today else 1
    end_offset = start_offset + days

    for day_offset in range(start_offset, end_offset):
        if stop_reason:
            break
        if write and download_count >= max_downloads:
            print(f"[schedule] reached max-downloads-per-run={max_downloads}; stopping write loop")
            stop_reason = "download_cap"
            break

        target_date = from_date + datetime.timedelta(days=day_offset)
        date_str = target_date.isoformat()

        if write:
            base = SOCIAL_QUEUE_DIR / date_str
            if base.is_dir():
                for folder in base.iterdir():
                    if folder.is_dir():
                        _folder_is_valid(folder, repair_missing=True, download_delay=download_delay)

        existing = _existing_rank_folders(date_str)
        max_existing_rank = max((r for r, _ in existing), default=0)

        posted_ranks_today_for_pack = {
            int(entry["rank"])
            for entry in log
            if entry.get("date") == date_str
            and entry.get("pack_id") == pack_id
            and entry.get("status") in {"posted", "posted_manual_backfill"}
            and isinstance(entry.get("rank"), int)
        }

        # Slots are capped by this pack's own quota, not the total folder count.
        # per_day further limits additions in a single run (useful for large pools).
        existing_date_slugs: set[str] = set()
        existing_date_source_urls: set[str] = set()
        existing_date_candidate_ids: set[str] = set()
        existing_date_text_hashes: set[str] = set()

        existing_details: list[dict] = []
        for rank_value, ef in existing:
            meta = _read_queue_metadata(ef)
            src_url = (meta.get("source_url") or meta.get("page_url") or "").strip()
            candidate_id = (meta.get("candidate_id") or "").strip()
            slug = re.sub(r"^\d+-(.+)$", r"\1", ef.name)
            try:
                post_text = (ef / "post.txt").read_text(encoding="utf-8").strip()
            except OSError:
                post_text = ""
            text_hash = hashlib.sha256(_normalize_text(post_text).encode()).hexdigest() if post_text else ""
            image_hash = hashlib.sha256((ef / "image.jpg").read_bytes()).hexdigest() if (ef / "image.jpg").is_file() else ""
            image_key = image_hash or src_url or candidate_id
            existing_details.append({
                "rank": rank_value,
                "folder": ef,
                "pack_id": meta.get("pack_id"),
                "slug": slug,
                "source_url": src_url,
                "candidate_id": candidate_id,
                "text_hash": text_hash,
                "image_key": image_key,
                "is_posted_today": rank_value in posted_ranks_today_for_pack and meta.get("pack_id") == pack_id,
            })

        slug_counter = Counter(detail["slug"] for detail in existing_details if detail["slug"])
        source_counter = Counter(detail["source_url"] for detail in existing_details if detail["source_url"])
        text_counter = Counter(detail["text_hash"] for detail in existing_details if detail["text_hash"])
        image_counter = Counter(detail["image_key"] for detail in existing_details if detail["image_key"])

        existing_for_pack = 0
        for detail in existing_details:
            if detail["pack_id"] != pack_id:
                continue
            is_viable = detail["is_posted_today"] or (
                (not detail["slug"] or slug_counter[detail["slug"]] == 1)
                and (not detail["source_url"] or source_counter[detail["source_url"]] == 1)
                and (not detail["text_hash"] or text_counter[detail["text_hash"]] == 1)
                and (not detail["image_key"] or image_counter[detail["image_key"]] == 1)
                and (not detail["slug"] or detail["slug"] not in posted_history["slugs"])
                and (not detail["source_url"] or detail["source_url"] not in posted_history["source_urls"])
                and (not detail["text_hash"] or detail["text_hash"] not in posted_history["text_hashes"])
                and (not detail["image_key"] or detail["image_key"] not in posted_history["image_keys"])
            )
            if is_viable:
                existing_for_pack += 1
                if detail["slug"]:
                    existing_date_slugs.add(detail["slug"])
                if detail["source_url"]:
                    existing_date_source_urls.add(detail["source_url"])
                if detail["candidate_id"]:
                    existing_date_candidate_ids.add(detail["candidate_id"])
                if detail["text_hash"]:
                    existing_date_text_hashes.add(detail["text_hash"])

        slots_to_fill = min(per_day, max(0, pack_daily_cap - existing_for_pack))
        remaining_pool = len(ranked_pool) - pool_idx

        print(
            f"[schedule] date={date_str}  existing_for_pack={existing_for_pack}  "
            f"target_for_pack={pack_daily_cap}  slots={slots_to_fill}  "
            f"remaining_fresh={remaining_pool}"
        )

        if slots_to_fill <= 0:
            schedule_days.append({
                "date": date_str,
                "existing_for_pack": existing_for_pack,
                "added": 0,
                "skipped_duplicate": 0,
                "items": [],
            })
            continue

        day_added = 0
        day_skipped_dup = 0
        day_items: list[dict] = []

        while day_added < slots_to_fill and pool_idx < len(ranked_pool):
            # Check per-item download cap before attempting this item
            if write and download_count >= max_downloads:
                stop_reason = "download_cap"
                break

            item = ranked_pool[pool_idx]
            pool_idx += 1

            title = _display_title(item)
            item_slug = _item_slug(item)
            candidate_id = (item.get("candidate_id") or "").strip()
            src_url = item.get("source_url") or item.get("page_url") or ""

            if item_slug in existing_date_slugs:
                day_skipped_dup += 1
                continue
            if candidate_id and candidate_id in existing_date_candidate_ids:
                day_skipped_dup += 1
                continue
            if src_url and src_url in existing_date_source_urls:
                day_skipped_dup += 1
                continue

            caption = _make_caption(item)
            if len(caption) > 280:
                caption = caption[:280].rstrip()
            caption_hash = hashlib.sha256(_normalize_text(caption).encode()).hexdigest()
            if caption_hash in existing_date_text_hashes:
                day_skipped_dup += 1
                continue

            rank = max_existing_rank + day_added + 1
            image_url = item.get("direct_image_url") or ""
            page_url = src_url
            folder_name = f"{rank:02d}-{item_slug}"

            day_item: dict = {
                "rank": rank,
                "folder_name": folder_name,
                "title": title,
                "image_url": image_url,
                "source_url": page_url,
                "caption": caption,
                "caption_chars": len(caption),
            }

            if write:
                try:
                    folder = _write_item(
                        date_str, rank, item, caption, pack_id,
                        download_delay=download_delay,
                    )
                except _RateLimitError:
                    print(f"[schedule] hit Wikimedia 429; stopping downloads until later")
                    stop_reason = "rate_limited"
                    break  # break inner while; day entry will be recorded below

                if folder is None:
                    day_item["status"] = "write_failed_or_exists"
                    day_items.append(day_item)
                    day_skipped_dup += 1
                    continue

                download_count += 1
                day_item["status"] = "written"
                print(f"  [ok] exports/social/{date_str}/{folder_name}/  [download {download_count}/{max_downloads}]")
            else:
                day_item["status"] = "planned"

            day_items.append(day_item)
            existing_date_slugs.add(item_slug)
            if candidate_id:
                existing_date_candidate_ids.add(candidate_id)
            if src_url:
                existing_date_source_urls.add(src_url)
            existing_date_text_hashes.add(caption_hash)
            day_added += 1

        print(
            f"[schedule] added={day_added}  skipped_duplicate={day_skipped_dup}  "
            f"remaining_fresh={len(ranked_pool) - pool_idx}"
        )
        print()

        schedule_days.append({
            "date": date_str,
            "existing_for_pack": existing_for_pack,
            "added": day_added,
            "skipped_duplicate": day_skipped_dup,
            "items": day_items,
        })

    total_added = sum(d["added"] for d in schedule_days)
    total_skipped = sum(d["skipped_duplicate"] for d in schedule_days)
    remaining = len(ranked_pool) - pool_idx
    stopped_due_to_rate_limit = (stop_reason == "rate_limited")

    print(f"[schedule] total added={total_added}  skipped={total_skipped}  remaining_pool={remaining}")
    print()

    if stop_reason == "rate_limited":
        print(
            f"[schedule] stopped early: Wikimedia 429 rate limit hit.  "
            f"Re-run after {retry_after_429}s ({retry_after_429 // 60}min) to continue filling."
        )
    elif stop_reason == "download_cap":
        print(
            f"[schedule] stopped early: reached max-downloads-per-run={max_downloads}.  "
            f"Re-run to continue filling remaining slots."
        )
    elif write:
        print(f"[schedule] write complete. {total_added} item(s) scheduled across {len(schedule_days)} day(s).")
    else:
        print(f"[schedule] dry run complete. Use --write to download images and create folders.")
    print()

    _write_manifest(
        pack_id, from_date_str, schedule_days, write,
        per_day, daily_post_cap, pack_daily_cap,
        stopped_due_to_rate_limit=stopped_due_to_rate_limit,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Schedule future social posts from a source-pack candidate pool."
    )
    parser.add_argument(
        "--pack", default="japanese_wood_historical",
        help="Pack ID (default: japanese_wood_historical)",
    )
    parser.add_argument(
        "--from-date", default="today",
        help=(
            "Base date: YYYY-MM-DD or 'today'. Fills from-date+1 through from-date+days by default; "
            "with --include-today it starts at from-date itself."
        ),
    )
    parser.add_argument(
        "--include-today", action="store_true",
        help="Include --from-date itself in the fill range. Use this to reschedule a pack for today.",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of future days to fill (default: 7)",
    )
    parser.add_argument(
        "--per-day", type=int, default=20,
        help="Maximum items to add per date in this run (default: 20)",
    )
    parser.add_argument(
        "--daily-post-cap", type=int, default=5,
        help="Global maximum posts per date used by post_daily_queue.py (default: 5); recorded in manifest",
    )
    parser.add_argument(
        "--pack-daily-cap", type=int, default=5,
        metavar="N",
        help=(
            "Maximum items this pack may occupy per date folder (default: 5). "
            "Only this pack's existing folders are counted, so multiple packs "
            "can each contribute their own quota to the same date."
        ),
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Download images and write queue folders. Dry-run by default.",
    )
    parser.add_argument(
        "--download-delay-seconds", type=float, default=5,
        metavar="N",
        help="Seconds to sleep between image downloads to avoid rate limits (default: 5)",
    )
    parser.add_argument(
        "--max-downloads-per-run", type=int, default=25,
        metavar="N",
        help="Stop after N successful image downloads per invocation (default: 25)",
    )
    parser.add_argument(
        "--retry-after-429-seconds", type=int, default=900,
        metavar="N",
        help="Suggested cooldown seconds printed when Wikimedia returns 429 (default: 900)",
    )
    parser.add_argument(
        "--refresh-if-empty", action="store_true",
        help=(
            "If the candidate export or fresh pool is empty, run the source-pack discovery "
            "pipeline in --discover-only mode and rebuild the candidate export."
        ),
    )
    parser.add_argument(
        "--refresh-max-total", type=int, default=40,
        metavar="N",
        help="Max candidates to discover when --refresh-if-empty is used (default: 40)",
    )
    args = parser.parse_args()

    try:
        from_date_str = _resolve_date(args.from_date)
    except ValueError:
        parser.error(f"Invalid --from-date: {args.from_date!r} — expected YYYY-MM-DD or 'today'")

    if args.days <= 0:
        parser.error("--days must be greater than 0")
    if args.per_day <= 0:
        parser.error("--per-day must be greater than 0")
    if args.daily_post_cap <= 0:
        parser.error("--daily-post-cap must be greater than 0")
    if args.pack_daily_cap <= 0:
        parser.error("--pack-daily-cap must be greater than 0")
    if args.download_delay_seconds < 0:
        parser.error("--download-delay-seconds must be >= 0")
    if args.max_downloads_per_run <= 0:
        parser.error("--max-downloads-per-run must be greater than 0")
    if args.retry_after_429_seconds <= 0:
        parser.error("--retry-after-429-seconds must be greater than 0")
    if args.refresh_max_total <= 0:
        parser.error("--refresh-max-total must be greater than 0")

    try:
        main(
            pack_id=args.pack,
            from_date_str=from_date_str,
            days=args.days,
            per_day=args.per_day,
            daily_post_cap=args.daily_post_cap,
            pack_daily_cap=args.pack_daily_cap,
            write=args.write,
            download_delay=args.download_delay_seconds,
            max_downloads=args.max_downloads_per_run,
            retry_after_429=args.retry_after_429_seconds,
            include_today=args.include_today,
            refresh_if_empty=args.refresh_if_empty,
            refresh_max_total=args.refresh_max_total,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[schedule] Error: {exc}", file=sys.stderr)
        sys.exit(1)
