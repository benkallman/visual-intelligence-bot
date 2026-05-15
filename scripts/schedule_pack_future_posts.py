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

Writes a manifest to:
  exports/social-schedule/<pack>/<from-date>/schedule.json
  exports/social-schedule/<pack>/<from-date>/schedule.md

Usage:
  python scripts/schedule_pack_future_posts.py --pack japanese_wood_historical \\
      --from-date today --days 7 --per-day 20
  python scripts/schedule_pack_future_posts.py --pack japanese_wood_historical \\
      --from-date today --days 3 --per-day 20 --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPTS_DIR)

from export_pack_social import (
    SOCIAL_PACKS_DIR,
    SOCIAL_QUEUE_DIR,
    _clean_text,
    _download_image,
    _is_raster_url,
    _load_from_disk,
    _load_from_export,
    _make_caption,
    _slug,
    _sort_key,
)

ROOT_DIR = Path(_SCRIPTS_DIR).parent
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"
SCHEDULE_DIR = ROOT_DIR / "exports" / "social-schedule"


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
                m = re.match(r"^\d+-(.+)$", folder.name)
                if m:
                    excluded_slugs.add(m.group(1))

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
        m = re.match(r"^(\d+)-", folder.name)
        if m:
            result.append((int(m.group(1)), folder))
    return sorted(result)


def _item_is_excluded(
    item: dict,
    excluded_slugs: set[str],
    excluded_urls: set[str],
) -> str | None:
    title = _clean_text(item.get("title") or "")
    candidate_slug = _slug(title)
    if candidate_slug in excluded_slugs:
        return f"slug already in use: {candidate_slug}"
    src_url = item.get("source_url") or item.get("page_url") or ""
    if src_url and src_url in excluded_urls:
        return f"source URL already in use: {src_url[:60]}"
    return None


def _write_item(
    date_str: str,
    rank: int,
    item: dict,
    caption: str,
    pack_id: str,
) -> Path | None:
    """Write post.txt, metadata.json, image.jpg. Returns folder path or None on failure."""
    title = _clean_text(item.get("title") or "Untitled")
    image_url = item.get("direct_image_url") or ""
    year = item.get("date_year")
    license_text = _clean_text(item.get("license") or "unknown")
    page_url = item.get("source_url") or item.get("page_url") or ""
    slug_str = _slug(title)
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

    ok = _download_image(image_url, folder / "image.jpg")
    if not ok:
        print(f"  [warn] image download failed for rank={rank}, removing folder")
        shutil.rmtree(folder, ignore_errors=True)
        return None

    return folder


def _write_manifest(
    pack_id: str,
    from_date_str: str,
    schedule_days: list[dict],
    write_mode: bool,
) -> None:
    out_dir = SCHEDULE_DIR / pack_id / from_date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "pack_id": pack_id,
        "from_date": from_date_str,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "write_mode": write_mode,
        "days": schedule_days,
    }
    json_path = out_dir / "schedule.json"
    json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Social Schedule: {pack_id}",
        f"",
        f"**From date:** {from_date_str}  ",
        f"**Generated:** {manifest['generated_at']}  ",
        f"**Mode:** {'write' if write_mode else 'dry-run'}",
        f"",
    ]
    for day in schedule_days:
        lines.append(f"## {day['date']}")
        lines.append(f"")
        lines.append(
            f"- Existing: {day['existing_count']}  "
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    pack_id: str,
    from_date_str: str,
    days: int,
    per_day: int,
    daily_post_cap: int,
    write: bool,
) -> None:
    sys.stdout.reconfigure(errors="replace")

    from_date = datetime.date.fromisoformat(from_date_str)
    mode_label = "write" if write else "dry-run"
    print(
        f"[schedule] pack={pack_id}  from_date={from_date_str}  days={days}  "
        f"per_day={per_day}  daily_post_cap={daily_post_cap}  mode={mode_label}"
    )
    print()

    # Load candidates from the pack export for from_date
    items = _load_from_export(pack_id, from_date_str)
    if items is not None:
        src = f"exports/source-packs/{pack_id}/{from_date_str}/candidates.json"
        print(f"[schedule] loaded {len(items)} candidates from {src}")
    else:
        items = _load_from_disk(pack_id)
        print(f"[schedule] loaded {len(items)} candidates from disk (fallback)")

    if not items:
        print(f"[schedule] no candidates found for pack={pack_id!r}")
        print(f"[schedule] run: python scripts/export_pack_candidates.py --pack {pack_id} --date {from_date_str}")
        return

    raster = [it for it in items if _is_raster_url(it.get("direct_image_url") or "")]
    n_non_raster = len(items) - len(raster)
    print(f"[schedule] {len(raster)} raster-image candidates ({n_non_raster} skipped -- no image URL or non-raster)")

    # Build global exclusion sets (log + all existing queue + staging)
    excluded_slugs, excluded_urls = _build_exclusion_sets(pack_id)
    print(
        f"[schedule] exclusion set: {len(excluded_slugs)} slugs  {len(excluded_urls)} source URLs  "
        f"(log + all queue folders + staging)"
    )

    # Filter to fresh candidates
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
        print("[schedule] no fresh candidates to schedule.")
        return

    ranked_pool = sorted(fresh, key=_sort_key, reverse=True)

    pool_idx = 0
    schedule_days: list[dict] = []

    for day_offset in range(1, days + 1):
        target_date = from_date + datetime.timedelta(days=day_offset)
        date_str = target_date.isoformat()

        existing = _existing_rank_folders(date_str)
        existing_count = len(existing)
        max_existing_rank = max((r for r, _ in existing), default=0)

        slots_to_fill = min(per_day, max(0, daily_post_cap - existing_count))
        remaining_pool = len(ranked_pool) - pool_idx

        print(
            f"[schedule] date={date_str}  existing={existing_count}  "
            f"slots={slots_to_fill}  remaining_fresh={remaining_pool}"
        )

        if slots_to_fill <= 0:
            schedule_days.append({
                "date": date_str,
                "existing_count": existing_count,
                "added": 0,
                "skipped_duplicate": 0,
                "items": [],
            })
            continue

        existing_date_slugs: set[str] = set()
        for _, ef in existing:
            m = re.match(r"^\d+-(.+)$", ef.name)
            if m:
                existing_date_slugs.add(m.group(1))

        day_added = 0
        day_skipped_dup = 0
        day_items: list[dict] = []

        while day_added < slots_to_fill and pool_idx < len(ranked_pool):
            item = ranked_pool[pool_idx]
            pool_idx += 1

            title = _clean_text(item.get("title") or "Untitled")
            item_slug = _slug(title)

            # Guard against slug collision within this date's folder
            if item_slug in existing_date_slugs:
                day_skipped_dup += 1
                continue

            caption = _make_caption(item)
            if len(caption) > 280:
                caption = caption[:280].rstrip()

            rank = max_existing_rank + day_added + 1
            image_url = item.get("direct_image_url") or ""
            page_url = item.get("source_url") or item.get("page_url") or ""
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
                folder = _write_item(date_str, rank, item, caption, pack_id)
                if folder is None:
                    day_item["status"] = "write_failed_or_exists"
                    day_items.append(day_item)
                    day_skipped_dup += 1
                    continue
                day_item["status"] = "written"
                print(f"  [ok] exports/social/{date_str}/{folder_name}/")
            else:
                day_item["status"] = "planned"

            day_items.append(day_item)
            existing_date_slugs.add(item_slug)
            day_added += 1

        print(
            f"[schedule] added={day_added}  skipped_duplicate={day_skipped_dup}  "
            f"remaining_fresh={len(ranked_pool) - pool_idx}"
        )
        print()

        schedule_days.append({
            "date": date_str,
            "existing_count": existing_count,
            "added": day_added,
            "skipped_duplicate": day_skipped_dup,
            "items": day_items,
        })

    total_added = sum(d["added"] for d in schedule_days)
    total_skipped = sum(d["skipped_duplicate"] for d in schedule_days)
    remaining = len(ranked_pool) - pool_idx

    print(f"[schedule] total added={total_added}  skipped={total_skipped}  remaining_pool={remaining}")
    print()

    if write:
        print(f"[schedule] write complete. {total_added} item(s) scheduled across {len(schedule_days)} day(s).")
    else:
        print(f"[schedule] dry run complete. Use --write to download images and create folders.")
    print()

    _write_manifest(pack_id, from_date_str, schedule_days, write)


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
        help="Base date: YYYY-MM-DD or 'today'. Scheduling fills from-date+1 through from-date+days (default: today)",
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
        help="Maximum total items per date folder; filling stops when existing + added reaches this cap (default: 5)",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Download images and write queue folders. Dry-run by default.",
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

    try:
        main(
            pack_id=args.pack,
            from_date_str=from_date_str,
            days=args.days,
            per_day=args.per_day,
            daily_post_cap=args.daily_post_cap,
            write=args.write,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[schedule] Error: {exc}", file=sys.stderr)
        sys.exit(1)
