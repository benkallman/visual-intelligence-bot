#!/usr/bin/env python3
"""
Export publishable content bundles for accepted records.

Usage:
    python scripts/export_content.py --date YYYY-MM-DD
    python scripts/export_content.py --date today
"""

import argparse
import datetime
import io
import json
import os
import re
import shutil
import sys
import unicodedata

import httpx
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
RECORDS_DIR = os.path.join(ROOT_DIR, "data", "records")
SOURCES_DIR = os.path.join(ROOT_DIR, "data", "sources")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports", "nightly")
EXPORTS_DIR = os.path.join(ROOT_DIR, "exports")

_IMAGE_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}
_SUCCESS_STATUSES = {"pending", "approved", "corrected"}


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _summary_path(date_str: str) -> str:
    return os.path.join(REPORTS_DIR, f"{date_str}-summary.json")


def _record_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'rec_', 1)}.json")


def _source_path(source_id: str) -> str:
    return os.path.join(SOURCES_DIR, f"{source_id}.json")


def _iter_record_paths() -> list[str]:
    if not os.path.isdir(RECORDS_DIR):
        return []
    return sorted(
        os.path.join(RECORDS_DIR, name)
        for name in os.listdir(RECORDS_DIR)
        if name.startswith("rec_") and name.endswith(".json")
    )


def _accepted_source_ids(date_str: str) -> list[str]:
    summary_path = _summary_path(date_str)
    if os.path.isfile(summary_path):
        summary = _load_json(summary_path)
        ids = [
            item["source_id"]
            for item in summary.get("items", [])
            if item.get("status") == "accepted"
        ]
        if ids:
            return ids

    accepted: list[str] = []
    for path in _iter_record_paths():
        record = _load_json(path)
        if not str(record.get("created_at", "")).startswith(date_str):
            continue
        if record.get("governance", {}).get("review_status") not in _SUCCESS_STATUSES:
            continue
        if not record.get("safety", {}).get("safe", False):
            continue
        accepted.append(record["source_id"])
    return accepted


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "untitled"


def _ensure_sentence(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _first_sentence(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    return parts[0]


def _color_phrase(colors: list[str]) -> str:
    cleaned = [str(color).strip().lower() for color in colors if str(color).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return f"{cleaned[0].capitalize()} tones"
    return f"{cleaned[0].capitalize()} and {cleaned[1]} tones"


def _build_caption(record: dict) -> str:
    pass1 = record.get("pass1", {})
    pass2 = record.get("pass2", {})

    first = _ensure_sentence(_first_sentence(pass1.get("description", "")))
    colors = pass1.get("dominant_colors", [])[:2]
    style = next(
        (str(item).strip() for item in pass2.get("archive_context_used", []) if str(item).strip()),
        "",
    )
    composition = _ensure_sentence(pass1.get("composition_notes", ""))

    if colors and style:
        second = f"{_color_phrase(colors)} keep the image close to {style}."
    elif colors:
        second = f"{_color_phrase(colors)} hold the composition in a steady register."
    elif style:
        second = f"The composition stays close to {style}, built from visible details rather than asserted meaning."
    else:
        second = composition

    second = _ensure_sentence(second)
    if second and second != first:
        return f"{first} {second}".strip()
    return first


def _unique_append(tags: list[str], value: str) -> None:
    if not value:
        return
    normalized = _slugify(value)
    if normalized and normalized not in tags:
        tags.append(normalized)


def _build_tags(record: dict, source: dict) -> list[str]:
    pass1 = record.get("pass1", {})
    pass2 = record.get("pass2", {})
    text = " ".join(
        [
            str(source.get("title") or ""),
            str(pass1.get("description") or ""),
            str(pass1.get("composition_notes") or ""),
            str(pass2.get("interpretive_notes") or ""),
            " ".join(str(item) for item in pass2.get("archive_context_used", [])),
        ]
    ).lower()

    tags: list[str] = []

    for item in pass2.get("archive_context_used", [])[:2]:
        _unique_append(tags, str(item))

    keyword_tags = [
        ("portrait", "portrait"),
        ("religious", "religious-art"),
        ("woman", "female-figure"),
        ("man", "male-figure"),
        ("figure", "figure"),
        ("skull", "skull"),
        ("book", "book"),
        ("altar", "altar"),
        ("church", "church-interior"),
        ("chapel", "chapel"),
        ("coat of arms", "heraldry"),
        ("heraldic", "heraldry"),
        ("shield", "shield"),
        ("beach", "beach"),
        ("boat", "boats"),
        ("landscape", "landscape"),
        ("frame", "framed"),
        ("magdalene", "magdalene"),
        ("annunciation", "annunciation"),
    ]
    for needle, tag in keyword_tags:
        if needle in text:
            _unique_append(tags, tag)

    if "serene" in text:
        _unique_append(tags, "serene")
    if "skull" in text or "religious" in text or "altar" in text:
        _unique_append(tags, "solemn")
    if "gold" in text or "heraldry" in tags or "framed" in tags:
        _unique_append(tags, "ornate")
    if "beach" in text or "crowd" in text or "running" in text:
        _unique_append(tags, "lively")

    for color in pass1.get("dominant_colors", [])[:3]:
        _unique_append(tags, str(color))

    if len(tags) < 5:
        for item in pass1.get("elements", []):
            element_text = str(item.get("element") or "").lower()
            for needle, tag in keyword_tags:
                if needle in element_text:
                    _unique_append(tags, tag)
            if len(tags) >= 5:
                break

    for fallback in ("painting", "art", "composition"):
        if len(tags) >= 5:
            break
        _unique_append(tags, fallback)

    return tags[:10]


def _image_bytes_for_source(source: dict) -> bytes:
    local_path = source.get("local_image_path")
    if local_path:
        path = local_path
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(ROOT_DIR, path))
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()

    image_url = source.get("image_url") or source.get("url")
    if not image_url:
        raise FileNotFoundError(f"No image path or URL for {source.get('source_id')}")

    response = httpx.get(image_url, headers=_IMAGE_HEADERS, follow_redirects=True, timeout=60)
    response.raise_for_status()
    return response.content


def _write_image(source: dict, export_dir: str) -> str:
    raw_bytes = _image_bytes_for_source(source)
    image = Image.open(io.BytesIO(raw_bytes))
    if image.mode != "RGB":
        image = image.convert("RGB")

    path = os.path.join(export_dir, "image.jpg")
    image.save(path, format="JPEG", quality=90, optimize=True)
    return path


def _export_dir(date_str: str, title: str, source_id: str) -> str:
    base = os.path.join(EXPORTS_DIR, date_str)
    slug = _slugify(title)
    path = os.path.join(base, slug)
    if os.path.isdir(path) or not os.path.exists(path):
        return path
    return os.path.join(base, f"{slug}-{source_id}")


def _export_record(date_str: str, source_id: str) -> dict | None:
    record_path = _record_path(source_id)
    source_path = _source_path(source_id)
    if not os.path.isfile(record_path) or not os.path.isfile(source_path):
        return None

    record = _load_json(record_path)
    source = _load_json(source_path)
    title = source.get("title") or record.get("record_id") or source_id
    export_dir = _export_dir(date_str, title, source_id)
    os.makedirs(export_dir, exist_ok=True)

    caption = _build_caption(record)
    tags = _build_tags(record, source)
    metadata = {
        "title": title,
        "artist": source.get("artist") or "",
        "source_url": source.get("url") or source.get("image_url") or "",
        "caption": caption,
        "tags": tags,
    }

    _write_image(source, export_dir)
    with open(os.path.join(export_dir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption + "\n")
    with open(os.path.join(export_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return {
        "source_id": source_id,
        "title": title,
        "export_dir": export_dir,
        "caption": caption,
        "tags": tags,
    }


def main(date_value: str) -> None:
    date_str = _resolve_date(date_value)
    source_ids = _accepted_source_ids(date_str)

    if not source_ids:
        print(f"[export] No accepted records found for {date_str}.")
        return

    results = []
    for source_id in source_ids:
        exported = _export_record(date_str, source_id)
        if exported is None:
            print(f"[export] SKIP missing record or source for {source_id}")
            continue
        results.append(exported)
        print(f"[export] {source_id} -> {exported['export_dir']}")

    print()
    print(f"[export] Exported {len(results)} item(s) for {date_str}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export publishable content bundles for accepted records.")
    parser.add_argument("--date", required=True, help="Date to export, in YYYY-MM-DD format or 'today'")
    args = parser.parse_args()
    main(args.date)
