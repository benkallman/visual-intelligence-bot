"""
Wikimedia Commons candidate discovery.

Given one or more Commons file-page URLs, fetches the direct image URL via
the MediaWiki API and returns a candidate record for each.
"""

import datetime
import hashlib
import json
import os
import re

import httpx

CANDIDATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "candidates")

SOURCE_REGISTRY_ID = "wikimedia_commons"

_API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
_FILE_PAGE_PATTERN = re.compile(
    r"https?://commons\.wikimedia\.org/wiki/(File:[^?#]+)", re.IGNORECASE
)


def _extract_file_title(page_url: str) -> str:
    """Extract 'File:Filename.ext' from a Commons file-page URL."""
    m = _FILE_PAGE_PATTERN.match(page_url.strip())
    if not m:
        raise ValueError(f"Not a recognized Commons file-page URL: {page_url!r}")
    return m.group(1)


def _fetch_image_info(file_title: str) -> dict:
    """Query the MediaWiki API for imageinfo on a single file title."""
    params = {
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiextmetadatafilter": "ObjectName",
        "format": "json",
    }
    response = httpx.get(_API_ENDPOINT, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        raise RuntimeError(f"Empty API response for {file_title!r}")
    page = next(iter(pages.values()))
    imageinfo = page.get("imageinfo")
    if not imageinfo:
        raise RuntimeError(f"No imageinfo returned for {file_title!r}")
    return page, imageinfo[0]


def _candidate_id(page_url: str) -> str:
    digest = hashlib.sha1(page_url.encode()).hexdigest()[:10]
    return f"cand_{digest}"


def discover_candidate(page_url: str) -> dict:
    """Fetch metadata for one Commons file-page URL and return a candidate record."""
    file_title = _extract_file_title(page_url)
    page, info = _fetch_image_info(file_title)

    extmeta = info.get("extmetadata", {})
    title = (
        extmeta.get("ObjectName", {}).get("value")
        or page.get("title")
        or file_title
    )

    return {
        "candidate_id": _candidate_id(page_url),
        "source_registry_id": SOURCE_REGISTRY_ID,
        "page_url": page_url.strip(),
        "direct_image_url": info["url"],
        "title": title,
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
    }


def save_candidate(record: dict) -> str:
    os.makedirs(CANDIDATES_DIR, exist_ok=True)
    path = os.path.join(CANDIDATES_DIR, f"{record['candidate_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path
