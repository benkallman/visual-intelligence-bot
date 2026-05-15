#!/usr/bin/env python3
"""
Export X-ready social draft posts from a source-pack candidate list.

Reads:
  exports/source-packs/<pack_id>/<date>/candidates.json  (preferred)
  data/candidates/cand_*.json  (fallback, filtered by pack_id)

Writes (staging area -- does not touch the main posting queue):
  exports/social-packs/<pack_id>/<date>/<rank:02d>-<slug>/
    post.txt       -- caption text, 280 chars max
    image.jpg      -- raster image downloaded from direct_image_url
    metadata.json  -- candidate fields + caption + rank

Default behavior: write staging files, do not copy to main queue.
Use --copy-to-social to promote into exports/social/<date>/ for post_daily_queue.py.
Use --dry-run to preview captions without writing any files.

Usage:
  python scripts/export_pack_social.py --pack japanese_wood_historical --date today --top 5
  python scripts/export_pack_social.py --pack japanese_wood_historical --date today --top 5 --dry-run
  python scripts/export_pack_social.py --pack japanese_wood_historical --date today --top 3 --copy-to-social
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = ROOT_DIR / "data" / "candidates"
SOURCE_PACKS_EXPORTS_DIR = ROOT_DIR / "exports" / "source-packs"
SOCIAL_PACKS_DIR = ROOT_DIR / "exports" / "social-packs"
SOCIAL_QUEUE_DIR = ROOT_DIR / "exports" / "social"
LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"

_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}

_RASTER_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"})

_OPEN_LICENSE_PATTERNS = ["public domain", "cc0", "cc by", "cc-by", "pdm", "pd-", "no restrictions"]
_CLOSED_LICENSE_PATTERNS = ["all rights reserved", "copyright"]

# Title keywords that boost candidate ranking
_PRIORITY_KEYWORDS = [
    "woodblock", "wood-block", "woodcut", "ukiyo-e", "ukiyoe",
    "netsuke", "noh", "buddhist", "edo", "meiji",
    "hokusai", "hiroshige", "kuniyoshi", "utamaro", "toshikata",
    "mokuhanga", "print",
    "yokai", "oni", "tengu", "ghost", "demon", "supernatural",
    "spirit", "folklore", "yoshitoshi", "kyosai", "kawanabe",
]

_SUPERNATURAL_KEYWORDS = frozenset({
    "yokai", "youkai", "oni", "tengu", "kappa", "yurei", "obake", "bakemono",
    "ghost", "demon", "spirit", "supernatural", "monster", "folklore", "mythology",
    "hyakki", "yagyo", "shoki", "exorcism", "magic", "mystic", "kitsune",
    "tanuki", "dragon", "kirin", "raijin", "fujin", "fudo",
})


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _license_score(license_text: str) -> int:
    lower = (license_text or "").lower()
    if any(p in lower for p in _OPEN_LICENSE_PATTERNS):
        return 2
    if any(p in lower for p in _CLOSED_LICENSE_PATTERNS):
        return -5
    return 0


def _year_score(year: int | None) -> int:
    if year is None:
        return 0
    return 1 if year <= 1956 else -1


def _keyword_score(title: str) -> int:
    lower = (title or "").lower()
    return sum(1 for kw in _PRIORITY_KEYWORDS if kw in lower)


def _sort_key(item: dict) -> tuple:
    lic = _license_score(item.get("license") or "")
    yr = _year_score(item.get("date_year"))
    kw = _keyword_score(item.get("title") or "")
    year_val = item.get("date_year") or 9999
    return (lic + yr + kw, -year_val)


# ---------------------------------------------------------------------------
# Caption generation
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    clean = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(clean.split())


def _detect_period(title: str, year: int | None) -> str:
    lower = title.lower()
    if "hokusai" in lower or "hiroshige" in lower or "utamaro" in lower or "kuniyoshi" in lower or "sharaku" in lower:
        return "Edo"
    if "kyosai" in lower or "kawanabe" in lower:
        return "Meiji"
    if "meiji" in lower or (year and 1868 <= year <= 1912):
        return "Meiji"
    if "taisho" in lower or (year and 1912 < year <= 1926):
        return "Taisho"
    if "yoshitoshi" in lower or "kunichika" in lower or "kunisada" in lower:
        return "Edo"
    if "edo" in lower or (year and year < 1868):
        return "Edo"
    return "Japanese"


def _opening_line(title: str, year: int | None) -> str:
    """Return a compact type/period/year opening sentence (ends with period)."""
    lower = title.lower()
    year_str = f", {year}" if year else ""

    if "netsuke" in lower:
        if "collection" in lower or "different" in lower or "set" in lower:
            return f"Netsuke collection{year_str}."
        return f"Netsuke{year_str}."

    if "noh" in lower and "mask" in lower:
        # Try to extract character name between "Noh Mask:" and following punctuation
        m = re.search(r"noh\s+mask[:\s]+([^,(]+)", title, re.IGNORECASE)
        char = m.group(1).strip() if m else ""
        if char and len(char) < 40:
            return f"Noh mask -- {char}{year_str}."
        return f"Noh mask{year_str}."

    if "shishi" in lower or ("guardian" in lower and "lion" in lower):
        return f"Buddhist guardian sculpture{year_str}."

    if "buddhist" in lower:
        return f"Buddhist carved wood{year_str}."

    is_print = any(kw in lower for kw in ["woodblock", "woodcut", "wood-block", "ukiyo-e", "print"])
    if any(kw in lower for kw in _SUPERNATURAL_KEYWORDS) and is_print:
        period = _detect_period(title, year)
        return f"{period} woodblock print{year_str}."

    if is_print:
        period = _detect_period(title, year)
        return f"{period} woodblock print{year_str}."

    return f"Historical work{year_str}."


def _subject_note(title: str) -> str:
    """Return a pointed 1-2 sentence observation from the title's subject matter."""
    lower = title.lower()

    # Subject-specific notes matched against common Wikimedia title patterns
    if "waterfall" in lower:
        return "Landscape subject -- vertical composition and water movement within the ukiyo-e tradition."
    if "laundry" in lower or ("hanging" in lower and "balcony" in lower):
        return "Domestic subject -- everyday ritual placed in the visual culture of Edo Japan."
    if "procession" in lower or ("festival" in lower and "woodblock" in lower):
        return "Festival scene -- communal movement and staging in Meiji figurative print design."
    if "rescue" in lower and "storm" in lower:
        return "Dramatic subject -- movement, weather, and human action compressed into print composition."
    if "waterwheel" in lower or "water wheel" in lower:
        return "Rural subject -- mechanical motion and labour rendered through woodblock composition."
    if "eavesdrop" in lower:
        return "Interior scene -- spatial staging and social detail in Meiji figurative print design."
    if "courtesan" in lower:
        return "Figure study -- costume, posture, and surface within the Edo bijin print tradition."
    if ("gangster" in lower or "hero" in lower) and "woodblock" in lower:
        return "Character print -- heroic figure, gesture, and narrative staging in popular Meiji print culture."
    if "archer" in lower or "kyujutsu" in lower:
        return "Martial arts subject -- figure, movement, and tournament staging connecting discipline and print design."
    if "earthquake" in lower:
        return "Disaster document in woodblock -- rapid print production recording the Ansei Edo earthquake for popular distribution."
    if "seven" in lower and ("god" in lower or "luck" in lower):
        return "Devotional subject -- the Seven Lucky Gods across popular print culture and festival imagery."
    if ("o'clock" in lower or "clock" in lower) and "woodblock" in lower:
        return "Time-of-day domestic scene -- hour and interior life marked through Meiji figurative print design."
    if "lanning" in lower or "new home" in lower:
        return "Domestic interior subject -- household arrangement and everyday ritual in Meiji print design."
    if "crane" in lower and ("inro" in lower or "cloud" in lower or "decoration" in lower):
        return "Decorative object -- crane imagery connecting longevity symbolism and surface ornament across Japanese craft."

    # Supernatural / folklore subject notes
    if "hyakki" in lower or "yagyo" in lower or "night parade" in lower:
        return "Folklore motif -- the Night Parade of One Hundred Demons, a procession of supernatural beings from Japanese folklore."
    if "shoki" in lower:
        return "Depicts Shoki the Demon Queller -- a protective deity shown vanquishing oni in Japanese folk belief and print tradition."
    if "benkei" in lower:
        return "Legendary martial scene -- Benkei the warrior monk, a figure of superhuman strength in Japanese folklore and kabuki theatre."
    if "tengu" in lower:
        return "Folklore motif -- tengu, mountain spirits with avian features, appear across Japanese mythology, kabuki, and woodblock print."
    if "kappa" in lower:
        return "Yokai imagery -- kappa, water-dwelling creatures from Japanese folklore, depicted across popular woodblock print culture."
    if "oni" in lower:
        return "Depicts oni, horned demons from Japanese folklore -- recurring figures across woodblock print, kabuki, and seasonal ritual."
    if "yurei" in lower or "ghost" in lower or "yūrei" in lower:
        return "Folklore motif -- yūrei, spirits of the dead in Japanese tradition, depicted with distinctive pale and trailing visual language."
    if "kitsune" in lower or ("fox" in lower and "spirit" in lower):
        return "Yokai imagery -- the kitsune fox spirit, a shape-shifting figure of intelligence and mischief in Japanese mythology."
    if "tanuki" in lower:
        return "Depicts tanuki, the raccoon dog, a trickster figure with magical transformation abilities in Japanese folklore."
    if "dragon" in lower and ("japanese" in lower or "woodblock" in lower or "print" in lower):
        return "Legendary martial scene -- the Japanese dragon, a water deity and symbol of power depicted across woodblock print tradition."
    if any(kw in lower for kw in ("yokai", "youkai", "obake", "bakemono")):
        return "Yokai imagery -- supernatural creatures from Japanese folklore rendered in woodblock print form."
    if "exorcism" in lower or ("demon" in lower and "quell" in lower):
        return "Depicts an exorcism or demon-quelling scene -- protective ritual power expressed through woodblock composition."
    if ("warrior monk" in lower or "yamabushi" in lower) and ("woodblock" in lower or "print" in lower):
        return "Legendary martial scene -- warrior monks embodying the intersection of religious discipline and martial power in Japanese imagery."
    if "samurai" in lower and any(kw in lower for kw in ("supernatural", "ghost", "demon", "spirit", "magic")):
        return "Legendary martial scene -- samurai confronting supernatural forces, a recurring dramatic subject in Edo and Meiji woodblock print."
    if "kabuki" in lower and any(kw in lower for kw in ("demon", "ghost", "supernatural", "oni", "spirit")):
        return "Kabuki/theatre scene -- supernatural antagonist rendered through the visual conventions of Japanese woodblock print."

    # Artist-specific notes (Wikimedia titles often name the artist)
    if "kyosai" in lower or "kawanabe" in lower:
        return "Kawanabe Kyōsai print -- Meiji satirist and master of supernatural imagery, bridging Edo tradition and modern irreverence."
    if "yoshitoshi" in lower:
        return "Tsukioka Yoshitoshi print -- the last great master of ukiyo-e, known for dramatic scenes of violence, ghosts, and human passion."
    if "hokusai" in lower:
        return "Hokusai composition -- dynamic line and naturalist observation defining the Edo woodblock tradition."
    if "hiroshige" in lower:
        return "Hiroshige landscape -- atmospheric depth and travel imagery across the Edo period."
    if "kuniyoshi" in lower or "kunimasa" in lower:
        return "Bold figure work and narrative staging in the Edo woodblock print tradition."
    if "utamaro" in lower:
        return "Intimate figure study and decorative surface in the Edo bijin woodblock tradition."
    if "sharaku" in lower:
        return "Theatrical portrait compressed into a bold kabuki woodblock image."
    if "shunkyo" in lower or "toshimine" in lower or "zeshin" in lower:
        return "Meiji figurative or landscape work -- bridging the Edo woodblock tradition and modern visual culture."
    if "toshikata" in lower:
        return "Mizuno Toshikata print -- Meiji figurative work bridging traditional and modern print output."
    if "josen" in lower or "hamada" in lower:
        return "Meiji domestic print -- figurative design connecting interior life and woodblock print culture."

    # Medium-specific fallbacks
    if "noh" in lower and "mask" in lower:
        return "Carved face with controlled expression -- designed to shift under stage light, marking theatrical identity."
    if "netsuke" in lower:
        return "Miniature carved forms showing how function, ornament, and storytelling compress into handheld scale."
    if "buddhist" in lower or "shishi" in lower:
        return "Ritual presence shaped through carved wood -- holding function across temple and monastery contexts."
    if any(kw in lower for kw in ["woodblock", "woodcut", "ukiyo-e", "print"]):
        period = "Meiji" if "meiji" in lower else ("Edo" if "edo" in lower else "")
        p = f"{period} " if period else ""
        return f"{p}Woodblock print -- line, pigment, and paper composing images before mechanical reproduction in Japan."

    return "Historical image -- medium and period from source metadata."


def _make_caption(item: dict) -> str:
    """Return a caption string under 280 chars."""
    title = _clean_text(item.get("title") or "Untitled")
    year = item.get("date_year")

    opening = _opening_line(title, year)
    note = _subject_note(title)

    caption = f"{opening} {note}"

    if len(caption) <= 280:
        return caption

    # Trim note to fit
    max_note = 279 - len(opening)
    if max_note < 15:
        return opening[:280]
    return f"{opening} {note[:max_note].rstrip()}"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _url_ext(url: str) -> str:
    path = url.split("?")[0].rstrip("/")
    return os.path.splitext(path)[1].lower()


def _is_raster_url(url: str) -> bool:
    return _url_ext(url) in _RASTER_EXTS


def _slug(title: str) -> str:
    title = re.sub(r"^File:", "", title, flags=re.IGNORECASE).strip()
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    return slug[:40].rstrip("-")


def _enrich(item: dict) -> dict:
    """Add direct_image_url from the candidate file if missing from the export JSON."""
    if item.get("direct_image_url"):
        return item
    cid = item.get("candidate_id")
    if not cid:
        return item
    cand_path = CANDIDATES_DIR / f"{cid}.json"
    if not cand_path.exists():
        return item
    try:
        cand = json.loads(cand_path.read_text(encoding="utf-8"))
        return {**item, "direct_image_url": cand.get("direct_image_url") or ""}
    except Exception:
        return item


def _load_from_export(pack_id: str, date_str: str) -> list[dict] | None:
    path = SOURCE_PACKS_EXPORTS_DIR / pack_id / date_str / "candidates.json"
    if not path.exists():
        return None
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        return [_enrich(it) for it in items]
    except Exception:
        return None


def _load_from_disk(pack_id: str) -> list[dict]:
    if not CANDIDATES_DIR.exists():
        return []
    results = []
    for path in sorted(CANDIDATES_DIR.glob("cand_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("pack_id") == pack_id:
            results.append(data)
    return results


def _download_image(url: str, dest: Path) -> bool:
    """Download a raster image to dest. Returns True on success."""
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "text/" in ct or "html" in ct:
            print(f"  [warn] URL returned HTML, not an image ({url[:60]})")
            return False
        dest.write_bytes(resp.content)
        return True
    except Exception as exc:
        print(f"  [warn] image download failed: {exc}")
        return False


def _next_social_rank(date_str: str) -> int:
    """Return the next unused rank number in exports/social/<date>/."""
    base = SOCIAL_QUEUE_DIR / date_str
    if not base.is_dir():
        return 1
    existing = []
    for folder in base.iterdir():
        m = re.match(r"^(\d+)-", folder.name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _find_existing_slug(date_str: str, slug: str) -> Path | None:
    """Return the queue folder whose slug suffix matches, or None.

    Matches any folder in exports/social/<date>/ of the form NN-<slug>,
    regardless of rank number.
    """
    base = SOCIAL_QUEUE_DIR / date_str
    if not base.is_dir():
        return None
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        m = re.match(r"^\d+-(.+)$", folder.name)
        if m and m.group(1) == slug:
            return folder
    return None


# ---------------------------------------------------------------------------
# Duplicate detection against the posting log
# ---------------------------------------------------------------------------

def _load_posted_signals() -> dict:
    """Return slug and source-URL sets extracted from data/social_post_log.json.

    Covers ALL entries (posted, skipped_duplicate, skipped_over_280, backfill)
    so candidates that were selected before but never went live are still blocked.
    """
    slugs: set[str] = set()
    source_urls: set[str] = set()

    if not LOG_PATH.is_file():
        return {"slugs": slugs, "source_urls": source_urls}

    try:
        log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"slugs": slugs, "source_urls": source_urls}

    for entry in log:
        folder = entry.get("folder", "")
        if folder:
            slug = re.sub(r"^\d{2}-", "", Path(folder).name)
            if slug:
                slugs.add(slug)
        url = entry.get("source_url") or ""
        if url:
            source_urls.add(url)

    return {"slugs": slugs, "source_urls": source_urls}


def _candidate_dup_reason(
    item: dict,
    signals: dict,
    queue_slugs: set[str],
    force: bool,
) -> str | None:
    """Return a human-readable reason if this candidate should be skipped, else None."""
    title = _clean_text(item.get("title") or "")
    candidate_slug = _slug(title)

    if candidate_slug in signals["slugs"]:
        return f"slug already posted: {candidate_slug}"

    src_url = item.get("source_url") or item.get("page_url") or ""
    if src_url and src_url in signals["source_urls"]:
        return f"source URL already posted: {src_url[:60]}"

    if not force and candidate_slug in queue_slugs:
        return f"already in today's queue: {candidate_slug}"

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(pack_id: str, date_str: str, top: int, dry_run: bool, copy_to_social: bool, force: bool = False) -> None:
    sys.stdout.reconfigure(errors="replace")

    # Load and enrich candidates
    items = _load_from_export(pack_id, date_str)
    if items is not None:
        src = f"exports/source-packs/{pack_id}/{date_str}/candidates.json"
        print(f"[pack-social] Loaded {len(items)} candidates from {src}")
    else:
        items = _load_from_disk(pack_id)
        print(f"[pack-social] Loaded {len(items)} candidates from disk (fallback)")

    if not items:
        print(f"[pack-social] No candidates found for pack={pack_id!r}.")
        print(f"[pack-social] Run: python scripts/export_pack_candidates.py --pack {pack_id} --date {date_str}")
        return

    # Filter: must have a raster direct_image_url
    raster = [it for it in items if _is_raster_url(it.get("direct_image_url") or "")]
    skipped = len(items) - len(raster)
    print(f"[pack-social] {len(raster)} raster-image candidates ({skipped} skipped -- no image URL or non-raster)")

    if not raster:
        print("[pack-social] No raster-image candidates. Check that candidate files have direct_image_url.")
        return

    # --- Duplicate filter: exclude anything already posted / skipped / in today's queue ---
    signals = _load_posted_signals()

    # Build the set of slugs already present in exports/social/<date>/.
    queue_slugs: set[str] = set()
    queue_base = SOCIAL_QUEUE_DIR / date_str
    if queue_base.is_dir():
        for _folder in queue_base.iterdir():
            if _folder.is_dir():
                m = re.match(r"^\d+-(.+)$", _folder.name)
                if m:
                    queue_slugs.add(m.group(1))

    fresh: list[dict] = []
    n_dup = 0
    for item in raster:
        reason = _candidate_dup_reason(item, signals, queue_slugs, force)
        if reason:
            title_short = _clean_text(item.get("title") or "")[:60]
            print(f"[pack-social] skipped already posted: {title_short}  reason={reason}")
            n_dup += 1
        else:
            fresh.append(item)

    print(f"[pack-social] fresh candidates after duplicate filter: {len(fresh)} ({n_dup} excluded)")

    if not fresh:
        print("[pack-social] No fresh candidates remaining after duplicate filter.")
        print("[pack-social] All candidates have already been posted or are in today's queue.")
        return

    # Score, rank, select top N from fresh candidates only
    ranked = sorted(fresh, key=_sort_key, reverse=True)
    selected = ranked[:top]

    mode_label = "dry run -- preview only" if dry_run else "writing to staging area"
    print(f"[pack-social] Selecting top {len(selected)} of {len(fresh)} fresh  [{mode_label}]")
    print()

    out_dir = SOCIAL_PACKS_DIR / pack_id / date_str
    exported: list[tuple[int, str]] = []   # (rank, folder_name)

    for rank, item in enumerate(selected, 1):
        title = _clean_text(item.get("title") or "Untitled")
        image_url = item.get("direct_image_url") or ""
        year = item.get("date_year")
        license_text = _clean_text(item.get("license") or "unknown")

        caption = _make_caption(item)
        char_count = len(caption)
        within = "OK" if char_count <= 280 else f"OVER by {char_count - 280}"

        slug_str = _slug(title)
        folder_name = f"{rank:02d}-{slug_str}"
        folder = out_dir / folder_name

        print(f"  [{rank:02d}] {title[:65]}")
        print(f"       year={year or '?'}  license={license_text[:28]}  chars={char_count} [{within}]")
        print(f"       image: {image_url[:72]}")
        print(f"       out:   {folder_name}/")
        print()
        print("       caption:")
        for line in caption.split("\n"):
            print(f"       | {line}")
        print()

        if dry_run:
            continue

        # Write staging files
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "post.txt").write_text(caption, encoding="utf-8")

        page_url = item.get("source_url") or item.get("page_url") or ""
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
            "caption_chars": char_count,
            "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        ok = _download_image(image_url, folder / "image.jpg")
        if not ok:
            print(f"  [skip] rank {rank} -- image download failed, folder removed")
            shutil.rmtree(folder, ignore_errors=True)
            continue

        exported.append((rank, folder_name))
        print(f"  [ok] {folder}")
        print()

    # Summary
    if dry_run:
        print(f"[pack-social] Dry run complete. {len(selected)} caption(s) previewed.")
        print()
        print(f"[pack-social] To write files:")
        print(f"  python scripts/export_pack_social.py --pack {pack_id} --date {date_str} --top {top}")
        return

    print(f"[pack-social] Exported {len(exported)} of {len(selected)} item(s) to:")
    print(f"  {out_dir}")

    if not exported:
        return

    print()
    if not copy_to_social:
        print(f"[pack-social] To add to the posting queue after review:")
        print(f"  python scripts/export_pack_social.py --pack {pack_id} --date {date_str} --top {top} --copy-to-social")
        return

    # Promote into the main social queue, skipping slugs already present.
    queue_date_dir = SOCIAL_QUEUE_DIR / date_str
    queue_date_dir.mkdir(parents=True, exist_ok=True)

    next_rank = _next_social_rank(date_str)
    copied = n_skipped = 0

    print(f"[pack-social] Copying {len(exported)} item(s) into exports/social/{date_str}/")
    for _, folder_name in exported:
        slug_part = folder_name.split("-", 1)[1] if "-" in folder_name else folder_name
        existing = _find_existing_slug(date_str, slug_part)

        if existing is not None:
            if not force:
                print(f"[pack-social] skip existing in social queue: {existing.name}")
                n_skipped += 1
                continue
            # --force: overwrite in place at the existing rank, not at a new rank.
            dst_folder = existing
            print(f"[pack-social] overwrite (--force): {existing.name}")
        else:
            social_rank = next_rank + copied
            dst_name = f"{social_rank:02d}-{slug_part}"
            dst_folder = queue_date_dir / dst_name

        src_folder = out_dir / folder_name
        shutil.copytree(src_folder, dst_folder, dirs_exist_ok=True)
        if existing is None:
            print(f"  -> exports/social/{date_str}/{dst_folder.name}/")
        copied += 1

    if n_skipped:
        print(f"[pack-social] {n_skipped} item(s) skipped (already in queue). Use --force to overwrite.")
    if copied:
        print()
        print(f"[pack-social] Social queue updated. Post with:")
        print(f"  python scripts/post_daily_queue.py --date {date_str} --send")
    elif not n_skipped:
        print(f"[pack-social] Nothing copied (no staged items).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export X-ready social draft posts from a source-pack candidate list."
    )
    parser.add_argument(
        "--pack", default="japanese_wood_historical",
        help="Pack ID (default: japanese_wood_historical)",
    )
    parser.add_argument(
        "--date", default="today",
        help="Date label: YYYY-MM-DD or 'today' (default: today)",
    )
    parser.add_argument(
        "--top", type=int, default=5,
        help="Number of items to export (default: 5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview captions and ranking without writing files or downloading images",
    )
    parser.add_argument(
        "--copy-to-social", action="store_true",
        help="Copy staging exports into exports/social/<date>/ for post_daily_queue.py",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With --copy-to-social: overwrite already-copied items in place instead of skipping",
    )
    args = parser.parse_args()
    try:
        main(
            pack_id=args.pack,
            date_str=_resolve_date(args.date),
            top=args.top,
            dry_run=args.dry_run,
            copy_to_social=args.copy_to_social,
            force=args.force,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[pack-social] Error: {exc}", file=sys.stderr)
        sys.exit(1)
