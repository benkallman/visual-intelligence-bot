#!/usr/bin/env python3
"""
Run a themed source pack: discover and ingest works from Wikimedia Commons
that match the pack's queries, date ceiling, and rights preference.

Reads:  data/source_packs/<pack_id>.json
Writes: data/candidates/cand_*.json  (one per accepted item, tagged with pack_id)
        data/sources/src_*.json       (via ingest.py subprocess)
        data/records/rec_*.json       (via ingest.py subprocess)

Does NOT post to X and does NOT touch post_daily_queue.py.

Each query in the pack JSON is either:
  {"type": "search",   "q": "<fulltext search term>"}
  {"type": "category", "url": "<Commons category page URL>"}

Date filtering:
  - Items with year metadata > date_max are skipped (skipped_date).
  - Items with no date are admitted only if their title, artist, or query
    contains a Japanese historical period keyword (heian, edo, meiji, etc.)
    or a medium keyword (ukiyo-e, woodblock, netsuke, noh mask).
  - Items with no date and no keyword are also skipped (skipped_date).

Usage:
    python scripts/run_source_pack.py --pack japanese_wood_historical
    python scripts/run_source_pack.py --pack japanese_wood_historical --max-total 10
    python scripts/run_source_pack.py --pack japanese_wood_historical --max-total 3 --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = ROOT_DIR / "data" / "candidates"
SOURCES_DIR = ROOT_DIR / "data" / "sources"
SOURCE_PACKS_DIR = ROOT_DIR / "data" / "source_packs"
INGEST_SCRIPT = Path(__file__).parent / "ingest.py"

_API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
_COMMONS_BASE = "https://commons.wikimedia.org/wiki/"
_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}

# Historical period keywords and media type keywords that imply pre-1956 Japanese work.
# The query label is included in the keyword check, so items found via a "ukiyo-e"
# query will pass even when their individual metadata lacks an explicit date.
_HISTORICAL_KEYWORDS = [
    "heian", "kamakura", "muromachi", "momoyama",
    "edo period", "edo-period",
    "meiji", "taisho", "early showa",
    "nara period", "asuka", "kofun", "jomon", "yayoi",
    "ukiyo-e", "ukiyoe", "woodblock", "mokuhanga", "woodcut",
    "netsuke", "noh mask", "noh", "hokusai", "hiroshige", "kuniyoshi", "utamaro",
]

# License substrings that indicate open/CC/PD rights.
_OPEN_LICENSE_PATTERNS = [
    "public domain", "cc0", "cc by", "cc-by", "cc sa", "cc-sa",
    "pd-", "pdm", "no restrictions",
]

# Skip images smaller than this in both dimensions (likely icons/thumbnails).
_MIN_IMAGE_PX = 300

# File extensions that are never raster images and must not be sent to ingest.py.
# Checked against both the Commons page URL (fast, pre-fetch) and the direct
# CDN URL returned by the API.
_NON_IMAGE_EXTS = frozenset({
    ".pdf", ".djvu", ".txt", ".zip",
    ".mp4", ".webm", ".ogg", ".ogv", ".oga",
    ".svg", ".wav", ".flac", ".mid", ".midi",
})

# Extensions that are positively known to be raster images.
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"})


# ---------------------------------------------------------------------------
# Wikimedia API helpers
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    text = html_lib.unescape(text or "")
    text = re.sub(
        r"<[^>]+style=[\"'][^\"']*display\s*:\s*none[^\"']*[\"'][^>]*>.*?</\w+>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _candidate_id(page_url: str) -> str:
    return "cand_" + hashlib.sha1(page_url.encode()).hexdigest()[:10]


def _search_wikimedia(query: str, limit: int) -> list[str]:
    """Fulltext-search Commons in the File namespace; return file-page URLs."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",
        "srlimit": str(min(limit, 50)),
        "format": "json",
    }
    try:
        resp = httpx.get(_API_ENDPOINT, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[source-pack]   search error: {exc}")
        return []
    results = resp.json().get("query", {}).get("search", [])
    return [
        _COMMONS_BASE + r["title"].replace(" ", "_")
        for r in results
        if r.get("title", "").startswith("File:")
    ]


def _expand_category(category_url: str, limit: int) -> list[str]:
    """Expand a Commons category URL into a list of file-page URLs."""
    m = re.match(r"https?://commons\.wikimedia\.org/wiki/(Category:[^?#]+)", category_url)
    if not m:
        print(f"[source-pack]   invalid category URL: {category_url}")
        return []
    category_title = m.group(1).replace("_", " ")
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category_title,
        "cmtype": "file",
        "cmlimit": str(min(limit, 500)),
        "format": "json",
    }
    try:
        resp = httpx.get(_API_ENDPOINT, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[source-pack]   category error: {exc}")
        return []
    members = resp.json().get("query", {}).get("categorymembers", [])
    return [
        _COMMONS_BASE + mem["title"].replace(" ", "_")
        for mem in members
        if mem.get("title", "").startswith("File:")
    ]


def _fetch_file_metadata(page_url: str) -> dict | None:
    """Fetch imageinfo + extended metadata for one Commons file-page URL.

    Returns a dict with url, title, artist, date_raw, license, width, height,
    or None on any API / network error.
    """
    file_title = re.sub(r"https?://commons\.wikimedia\.org/wiki/", "", page_url).replace("_", " ")
    params = {
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiextmetadatafilter": "ObjectName|Artist|DateTimeOriginal|LicenseShortName",
        "format": "json",
    }
    try:
        resp = httpx.get(_API_ENDPOINT, params=params, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[source-pack]   metadata error ({page_url}): {exc}")
        return None
    pages = resp.json().get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    imageinfo = page.get("imageinfo")
    if not imageinfo:
        return None
    info = imageinfo[0]
    extmeta = info.get("extmetadata", {})
    return {
        "page_url": page_url,
        "url": info.get("url", ""),
        "mime": info.get("mime", ""),
        "width": info.get("width") or 0,
        "height": info.get("height") or 0,
        "title": _strip_html(extmeta.get("ObjectName", {}).get("value") or page.get("title") or file_title),
        "artist": _strip_html(extmeta.get("Artist", {}).get("value") or ""),
        "date_raw": _strip_html(extmeta.get("DateTimeOriginal", {}).get("value") or ""),
        "license": _strip_html(extmeta.get("LicenseShortName", {}).get("value") or ""),
    }


# ---------------------------------------------------------------------------
# Non-image detection
# ---------------------------------------------------------------------------

def _url_file_ext(url: str) -> str:
    """Return the lowercase extension from a URL, stripping any query string."""
    path = url.split("?")[0].rstrip("/")
    return os.path.splitext(path)[1].lower()


def _is_non_image(url: str, mime: str = "") -> bool:
    """Return True when the URL or MIME type indicates a non-raster-image file.

    Two-stage: extension check first (cheap), then MIME type if the API
    returned one. An empty or absent MIME is not treated as a rejection so
    that files whose type is unknown are not silently dropped.
    """
    if _url_file_ext(url) in _NON_IMAGE_EXTS:
        return True
    if mime and not mime.startswith("image/"):
        return True
    return False


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _parse_year(date_text: str) -> int | None:
    """Extract the latest 4-digit year from free-text date metadata.

    Uses the maximum year found so that date ranges like "1800-1900" are
    treated conservatively (1900 is the binding date for the ceiling check).
    Returns None when no year can be parsed.
    """
    if not date_text:
        return None
    clean = _strip_html(date_text)
    years = [int(y) for y in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", clean)]
    return max(years) if years else None


def _has_historical_keyword(text: str) -> bool:
    """Return True if text contains a known pre-1956 Japanese period/medium keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _HISTORICAL_KEYWORDS)


def _is_open_license(license_text: str) -> bool:
    lower = (license_text or "").lower()
    return any(pat in lower for pat in _OPEN_LICENSE_PATTERNS)


# ---------------------------------------------------------------------------
# Candidate persistence
# ---------------------------------------------------------------------------

def _already_known(page_url: str) -> bool:
    """Return True if this URL already has a candidate or source record on disk."""
    cid = _candidate_id(page_url)
    sid = cid.replace("cand_", "src_", 1)
    return (CANDIDATES_DIR / f"{cid}.json").exists() or (SOURCES_DIR / f"{sid}.json").exists()


def _save_candidate(meta: dict, pack_id: str, query: str, date_year: int | None) -> Path:
    cid = _candidate_id(meta["page_url"])
    record = {
        "candidate_id": cid,
        "source_registry_id": "wikimedia_commons",
        "page_url": meta["page_url"],
        "direct_image_url": meta["url"],
        "title": meta["title"],
        "artist": meta["artist"] or None,
        "width": meta["width"],
        "height": meta["height"],
        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "pack_id": pack_id,
        "pack_query": query,
        "date_raw": meta["date_raw"] or None,
        "date_year": date_year,
        "license": meta["license"] or None,
    }
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATES_DIR / f"{cid}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _run_ingest(candidate: dict, dry_run: bool) -> int:
    """Invoke ingest.py for one candidate; return its exit code."""
    source_id = candidate["candidate_id"].replace("cand_", "src_", 1)
    source_url = candidate.get("direct_image_url") or candidate["page_url"]
    cmd = [sys.executable, str(INGEST_SCRIPT), "--source-url", source_url, "--source-id", source_id]
    if candidate.get("title"):
        cmd += ["--title", candidate["title"]]
    if dry_run:
        cmd += ["--dry-run"]
    return subprocess.run(cmd, capture_output=False).returncode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(pack_id: str, max_total: int, per_query_limit: int, dry_run: bool) -> None:
    # Force line-buffering so [source-pack] lines are not held in Python's
    # stdout buffer while a subprocess (ingest.py) writes directly to the fd.
    sys.stdout.reconfigure(line_buffering=True)

    pack_path = SOURCE_PACKS_DIR / f"{pack_id}.json"
    if not pack_path.exists():
        raise FileNotFoundError(f"Source pack not found: {pack_path}")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    date_max = pack.get("date_max", 1956)
    queries = pack.get("queries", [])

    print(f"[source-pack] pack={pack_id}")
    print(f"[source-pack] label={pack['label']}")
    print(f"[source-pack] date_max={date_max}")
    print(f"[source-pack] queries={len(queries)}")
    print(f"[source-pack] max_total={max_total}  dry_run={dry_run}")
    print()

    new_candidates: list[dict] = []
    total_accepted = total_skip_date = total_skip_rights = total_skip_known = 0
    total_skip_non_image = total_errored = 0

    for entry in queries:
        if total_accepted >= max_total:
            print(f"[source-pack] max_total={max_total} reached -- stopping discovery.")
            break

        q_type = entry.get("type", "search")
        q_label = entry.get("q") or entry.get("url", "")
        print(f"[source-pack] query={q_label!r} (type={q_type})")

        if q_type == "category":
            file_urls = _expand_category(entry["url"], limit=per_query_limit)
        else:
            file_urls = _search_wikimedia(q_label, limit=per_query_limit)

        print(f"[source-pack]   found {len(file_urls)} file(s)")

        q_acc = q_sd = q_sr = q_sk = q_sni = q_err = 0

        for url in file_urls:
            if total_accepted >= max_total:
                break

            if _already_known(url):
                q_sk += 1
                total_skip_known += 1
                continue

            # Fast pre-fetch check: reject obvious non-images by page URL extension
            # before spending an API call on metadata.
            if _is_non_image(url):
                q_sni += 1
                total_skip_non_image += 1
                print(f"[source-pack]   skipped_non_image (url ext): {url.split('/')[-1][:60]}")
                continue

            meta = _fetch_file_metadata(url)
            if meta is None:
                q_err += 1
                total_errored += 1
                continue

            # Post-fetch check: verify CDN URL extension and MIME type.
            if _is_non_image(meta["url"], meta.get("mime", "")):
                q_sni += 1
                total_skip_non_image += 1
                print(
                    f"[source-pack]   skipped_non_image (mime={meta.get('mime', '?')}): "
                    f"{meta['title'][:55]}"
                )
                continue

            # Skip tiny images (icons, thumbnails, signatures).
            if meta["width"] < _MIN_IMAGE_PX and meta["height"] < _MIN_IMAGE_PX:
                q_err += 1
                total_errored += 1
                continue

            # --- Date filter ---
            date_year = _parse_year(meta["date_raw"])
            if date_year is not None:
                if date_year > date_max:
                    q_sd += 1
                    total_skip_date += 1
                    continue
            else:
                # No parseable date — allow only when the context implies a
                # pre-1956 Japanese historical period or medium.
                context = f"{meta['title']} {meta['artist']} {meta['date_raw']} {q_label}"
                if not _has_historical_keyword(context):
                    q_sd += 1
                    total_skip_date += 1
                    continue

            # --- Rights filter --- prefer open; skip if explicitly closed
            license_text = meta.get("license") or ""
            if license_text and not _is_open_license(license_text):
                q_sr += 1
                total_skip_rights += 1
                continue

            # Accept
            path = _save_candidate(meta, pack_id, q_label, date_year)
            candidate = json.loads(path.read_text(encoding="utf-8"))
            new_candidates.append(candidate)
            q_acc += 1
            total_accepted += 1
            lic_display = (license_text[:25] + "...") if len(license_text) > 25 else license_text
            print(
                f"[source-pack]   accepted: {meta['title'][:55]!r} "
                f"(year={date_year or '?'}, license={lic_display or 'unknown'})"
            )

        print(
            f"[source-pack]   accepted={q_acc} skipped_date={q_sd} "
            f"skipped_rights={q_sr} skipped_non_image={q_sni} "
            f"skipped_known={q_sk} errored={q_err}"
        )

    print()
    print("[source-pack] Discovery complete:")
    print(f"[source-pack]   accepted={total_accepted}")
    print(f"[source-pack]   skipped_date={total_skip_date}")
    print(f"[source-pack]   skipped_rights={total_skip_rights}")
    print(f"[source-pack]   skipped_non_image={total_skip_non_image}")
    print(f"[source-pack]   skipped_known={total_skip_known}")
    print(f"[source-pack]   errored={total_errored}")

    if not new_candidates:
        print("[source-pack] No new candidates — nothing to ingest.")
        return

    print(f"\n[source-pack] Ingesting {len(new_candidates)} new candidate(s)...")
    ingest_counts = {"processed": 0, "rejected": 0, "errored": 0}

    for candidate in new_candidates:
        cid = candidate["candidate_id"]
        sid = cid.replace("cand_", "src_", 1)
        if (SOURCES_DIR / f"{sid}.json").exists():
            print(f"[source-pack]   SKIP {cid} (already ingested)")
            continue
        title_short = (candidate.get("title") or "")[:50]
        print(f"[source-pack]   ingest {cid}: {title_short!r}")
        code = _run_ingest(candidate, dry_run)
        label = {0: "OK", 2: "FLAGGED", 3: "REJECTED"}.get(code, f"ERROR({code})")
        print(f"[source-pack]   -> {label}")
        if code in (0, 2):
            ingest_counts["processed"] += 1
        elif code == 3:
            ingest_counts["rejected"] += 1
        else:
            ingest_counts["errored"] += 1

    print()
    print("[source-pack] Ingest complete:")
    print(f"[source-pack]   processed={ingest_counts['processed']}")
    print(f"[source-pack]   rejected={ingest_counts['rejected']}")
    print(f"[source-pack]   errored={ingest_counts['errored']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover and ingest Wikimedia Commons works from a source pack."
    )
    parser.add_argument("--pack", required=True, help="Pack ID (matches data/source_packs/<pack>.json)")
    parser.add_argument("--max-total", type=int, default=20, help="Max candidates to accept across all queries (default: 20)")
    parser.add_argument("--per-query", type=int, default=20, help="Max files to fetch per query (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Save candidates but skip ingest.py calls")
    args = parser.parse_args()
    try:
        main(
            pack_id=args.pack,
            max_total=args.max_total,
            per_query_limit=args.per_query,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[source-pack] Error: {exc}", file=sys.stderr)
        sys.exit(1)
