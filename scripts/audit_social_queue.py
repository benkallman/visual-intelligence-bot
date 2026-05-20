#!/usr/bin/env python3
"""Audit today's social queue by source pack."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SOCIAL_QUEUE_DIR = ROOT_DIR / "exports" / "social"
SOURCE_PACKS_DIR = ROOT_DIR / "data" / "source_packs"
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"
MAX_POST_CHARS = 280

_POSTED_STATUSES = frozenset({"posted", "posted_manual_backfill"})
_DONE_STATUSES = frozenset({
    "posted",
    "posted_manual_backfill",
    "skipped_duplicate",
    "skipped_over_280",
    "skipped_bad_image",
})


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode()).hexdigest()


def _image_hash(image_path: Path) -> str | None:
    if not image_path.is_file():
        return None
    try:
        return hashlib.sha256(image_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_metadata(folder: Path) -> dict:
    return _load_json(folder / "metadata.json", {})


def _read_post_text(folder: Path) -> str:
    post_path = folder / "post.txt"
    if not post_path.is_file():
        return ""
    try:
        return post_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _folder_rank(folder: Path) -> int | None:
    match = re.match(r"^(\d+)-", folder.name)
    return int(match.group(1)) if match else None


def _folder_slug(folder: Path) -> str:
    return re.sub(r"^\d+-", "", folder.name)


def _history_signals(log: list[dict]) -> dict[str, set[str]]:
    image_keys: set[str] = set()
    source_urls: set[str] = set()
    text_hashes: set[str] = set()
    slugs: set[str] = set()

    for entry in log:
        if entry.get("status") not in _POSTED_STATUSES:
            continue
        folder = Path(entry.get("folder") or "")
        if str(folder):
            slug = _folder_slug(folder)
            if slug:
                slugs.add(slug)
        src_url = str(entry.get("source_url") or "").strip()
        if src_url:
            source_urls.add(src_url)
            image_keys.add(src_url)
        img_hash = str(entry.get("image_sha256") or "").strip()
        if img_hash:
            image_keys.add(img_hash)
        txt_hash = str(entry.get("post_text_sha256") or "").strip()
        if txt_hash:
            text_hashes.add(txt_hash)

    return {
        "image_keys": image_keys,
        "source_urls": source_urls,
        "text_hashes": text_hashes,
        "slugs": slugs,
    }


def _pack_ids() -> list[str]:
    return sorted(path.stem for path in SOURCE_PACKS_DIR.glob("*.json"))


def main(date_value: str) -> int:
    date_str = _resolve_date(date_value)
    base = SOCIAL_QUEUE_DIR / date_str
    log = _load_json(LOG_PATH, [])
    history = _history_signals(log)

    posted_today_by_pack = Counter()
    done_ranks_by_pack: dict[str, set[int]] = defaultdict(set)
    for entry in log:
        pack_id = str(entry.get("pack_id") or "").strip()
        rank = entry.get("rank")
        if not pack_id or entry.get("date") != date_str:
            continue
        if entry.get("status") in _POSTED_STATUSES:
            posted_today_by_pack[pack_id] += 1
        if entry.get("status") in _DONE_STATUSES and isinstance(rank, int):
            done_ranks_by_pack[pack_id].add(rank)

    folders_by_pack: dict[str, list[dict]] = defaultdict(list)
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            rank = _folder_rank(folder)
            if rank is None:
                continue
            meta = _read_metadata(folder)
            pack_id = str(meta.get("pack_id") or "unassigned").strip()
            post_text = _read_post_text(folder)
            text_hash = _text_hash(post_text) if post_text else ""
            source_url = str(meta.get("source_url") or meta.get("page_url") or "").strip()
            img_hash = _image_hash(folder / "image.jpg")
            image_key = img_hash or source_url or str(meta.get("candidate_id") or "")
            folders_by_pack[pack_id].append({
                "rank": rank,
                "folder": folder,
                "slug": _folder_slug(folder),
                "meta": meta,
                "has_image": (folder / "image.jpg").is_file(),
                "post_text": post_text,
                "text_hash": text_hash,
                "source_url": source_url,
                "image_key": image_key,
                "image_hash": img_hash,
            })

    print(f"[audit] date={date_str}")
    for pack_id in _pack_ids():
        items = sorted(folders_by_pack.get(pack_id, []), key=lambda item: item["rank"])
        text_counter = Counter(item["text_hash"] for item in items if item["text_hash"])
        image_counter = Counter(item["image_key"] for item in items if item["image_key"])

        missing_image_count = sum(1 for item in items if not item["has_image"])
        duplicate_text_count = sum(
            1
            for item in items
            if item["text_hash"]
            and (
                text_counter[item["text_hash"]] > 1
                or item["text_hash"] in history["text_hashes"]
            )
        )
        duplicate_image_count = sum(
            1
            for item in items
            if item["image_key"]
            and (
                image_counter[item["image_key"]] > 1
                or item["image_key"] in history["image_keys"]
                or (item["source_url"] and item["source_url"] in history["source_urls"])
            )
        )

        eligible_ranks: list[int] = []
        for item in items:
            rank = item["rank"]
            if rank in done_ranks_by_pack.get(pack_id, set()):
                continue
            if not item["has_image"] or not item["post_text"]:
                continue
            if len(item["post_text"]) > MAX_POST_CHARS:
                continue
            if item["slug"] and item["slug"] in history["slugs"]:
                continue
            if item["source_url"] and item["source_url"] in history["source_urls"]:
                continue
            if item["text_hash"] and (
                item["text_hash"] in history["text_hashes"]
                or text_counter[item["text_hash"]] > 1
            ):
                continue
            if item["image_key"] and (
                item["image_key"] in history["image_keys"]
                or image_counter[item["image_key"]] > 1
            ):
                continue
            eligible_ranks.append(rank)

        eligible_display = ",".join(str(rank) for rank in eligible_ranks[:10]) if eligible_ranks else "none"
        print(
            f"pack={pack_id}  folders={len(items)}  missing_image={missing_image_count}  "
            f"duplicate_text={duplicate_text_count}  duplicate_image={duplicate_image_count}  "
            f"eligible_next_ranks={eligible_display}  posted_today={posted_today_by_pack.get(pack_id, 0)}"
        )

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit social queue eligibility by pack.")
    parser.add_argument("--date", default="today", help="Date label: YYYY-MM-DD or 'today' (default: today)")
    args = parser.parse_args()
    raise SystemExit(main(args.date))
