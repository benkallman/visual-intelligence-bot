"""
Wikimedia Commons candidate discovery.

Given one or more Commons file-page URLs, fetches the direct image URL via
the MediaWiki API and returns a candidate record for each.
"""

import datetime
import hashlib
import html as html_lib
import json
import os
import re

import httpx

CANDIDATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "candidates")

SOURCE_REGISTRY_ID = "wikimedia_commons"

_API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
_COMMONS_BASE = "https://commons.wikimedia.org/wiki/"
_FILE_PAGE_PATTERN = re.compile(
    r"https?://commons\.wikimedia\.org/wiki/(File:[^?#]+)", re.IGNORECASE
)
_CATEGORY_PAGE_PATTERN = re.compile(
    r"https?://commons\.wikimedia\.org/wiki/(Category:[^?#]+)", re.IGNORECASE
)
_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}


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
        "iiprop": "url|size|extmetadata",
        "iiextmetadatafilter": "ObjectName|Artist",
        "format": "json",
    }
    try:
        response = httpx.get(_API_ENDPOINT, params=params, headers=_HEADERS, timeout=20)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise RuntimeError(
                f"Wikimedia API returned 403 for {file_title!r}. "
                "This is likely a User-Agent policy violation — ensure the request sends a valid User-Agent."
            ) from exc
        raise
    data = response.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        raise RuntimeError(f"Empty API response for {file_title!r}")
    page = next(iter(pages.values()))
    imageinfo = page.get("imageinfo")
    if not imageinfo:
        raise RuntimeError(f"No imageinfo returned for {file_title!r}")
    return page, imageinfo[0]


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities from a Wikimedia metadata string.

    Removes display:none elements first (Wikimedia embeds QS metadata labels
    in hidden divs that produce noise when the tags are stripped naively).
    """
    text = html_lib.unescape(text)
    # Drop entire elements marked display:none before stripping tags.
    text = re.sub(
        r"<[^>]+style=[\"'][^\"']*display\s*:\s*none[^\"']*[\"'][^>]*>.*?</\w+>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _metadata_text(extmeta: dict, key: str) -> str | None:
    value = extmeta.get(key, {}).get("value")
    if not value:
        return None
    text = _strip_html(value)
    return text or None


def _candidate_id(page_url: str) -> str:
    digest = hashlib.sha1(page_url.encode()).hexdigest()[:10]
    return f"cand_{digest}"


def discover_from_category(category_url: str, limit: int = 10) -> list[str]:
    """Return up to `limit` file-page URLs from a Commons category page URL."""
    m = _CATEGORY_PAGE_PATTERN.match(category_url.strip())
    if not m:
        raise ValueError(f"Not a recognized Commons category URL: {category_url!r}")
    category_title = m.group(1).replace("_", " ")

    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category_title,
        "cmtype": "file",
        "cmlimit": min(limit, 500),
        "format": "json",
    }
    try:
        response = httpx.get(_API_ENDPOINT, params=params, headers=_HEADERS, timeout=20)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise RuntimeError(
                f"Wikimedia API returned 403 for category {category_title!r}. "
                "This is likely a User-Agent policy violation — ensure the request sends a valid User-Agent."
            ) from exc
        raise

    members = response.json().get("query", {}).get("categorymembers", [])
    return [
        _COMMONS_BASE + member["title"].replace(" ", "_")
        for member in members
        if member.get("title", "").startswith("File:")
    ]


def discover_candidate(page_url: str) -> dict:
    """Fetch metadata for one Commons file-page URL and return a candidate record."""
    file_title = _extract_file_title(page_url)
    page, info = _fetch_image_info(file_title)

    extmeta = info.get("extmetadata", {})
    title = _metadata_text(extmeta, "ObjectName") or page.get("title") or file_title
    artist = _metadata_text(extmeta, "Artist")

    return {
        "candidate_id": _candidate_id(page_url),
        "source_registry_id": SOURCE_REGISTRY_ID,
        "page_url": page_url.strip(),
        "direct_image_url": info["url"],
        "title": title,
        "artist": artist,
        "width": info.get("width"),
        "height": info.get("height"),
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
    }


def save_candidate(record: dict) -> str:
    os.makedirs(CANDIDATES_DIR, exist_ok=True)
    path = os.path.join(CANDIDATES_DIR, f"{record['candidate_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path
