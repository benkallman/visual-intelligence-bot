#!/usr/bin/env python3
"""
Select the best social-ready content bundles from existing archive records.

Usage:
    python scripts/select_best_content.py --top 3
    python scripts/select_best_content.py --top 5 --date 2026-05-10
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
RECORDS_DIR = os.path.join(ROOT_DIR, "data", "records")
RARITY_DIR = os.path.join(ROOT_DIR, "data", "rarity")
SOURCES_DIR = os.path.join(ROOT_DIR, "data", "sources")
EXPORTS_DIR = os.path.join(ROOT_DIR, "exports")
SOCIAL_EXPORTS_DIR = os.path.join(EXPORTS_DIR, "social")

DEFAULT_TOP = 3
HASHTAGS = "#art #visualintelligence #rareimage"
COMBINED_SCORE_FORMULA = "0.45 * rarity_score + 0.35 * viral_score + 0.20 * brand_fit"


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: dict | list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _iter_record_paths() -> list[str]:
    if not os.path.isdir(RECORDS_DIR):
        return []
    return sorted(
        os.path.join(RECORDS_DIR, name)
        for name in os.listdir(RECORDS_DIR)
        if name.startswith("rec_") and name.endswith(".json")
    )


def _source_path(source_id: str) -> str:
    return os.path.join(SOURCES_DIR, f"{source_id}.json")


def _rarity_path(source_id: str) -> str:
    return os.path.join(RARITY_DIR, f"{source_id.replace('src_', 'rdt_', 1)}.json")


def _viral_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'vir_', 1)}.json")


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
        second = (
            f"The composition stays close to {style}, built from visible details rather than asserted meaning."
        )
    else:
        second = composition

    second = _ensure_sentence(second)
    if second and second != first:
        return f"{first} {second}".strip()
    return first


def _local_image_from_source(source: dict) -> str | None:
    local_path = source.get("local_image_path")
    if not local_path:
        return None
    candidate = local_path
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(os.path.join(ROOT_DIR, candidate))
    if os.path.isfile(candidate):
        return candidate
    return None


def _find_exported_image(source: dict, date_str: str) -> str | None:
    title = source.get("title") or source.get("source_id") or "untitled"
    slug = _slugify(title)
    source_url = source.get("url") or source.get("image_url") or ""

    direct_candidates = [
        os.path.join(EXPORTS_DIR, date_str, slug, "image.jpg"),
        os.path.join(EXPORTS_DIR, date_str, f"{slug}-{source['source_id']}", "image.jpg"),
    ]
    for path in direct_candidates:
        if os.path.isfile(path):
            return path

    for root, _, files in os.walk(EXPORTS_DIR):
        if "metadata.json" not in files or "image.jpg" not in files:
            continue
        metadata = _load_json(os.path.join(root, "metadata.json")) or {}
        if metadata.get("source_url") == source_url and source_url:
            return os.path.join(root, "image.jpg")
        if metadata.get("title") == title:
            return os.path.join(root, "image.jpg")

    return None


def _find_image_path(source: dict, date_str: str) -> str | None:
    return _local_image_from_source(source) or _find_exported_image(source, date_str)


def _post_text(title: str, caption: str, source_url: str) -> str:
    return "\n".join(
        [
            title.strip(),
            "",
            caption.strip(),
            "",
            "—",
            f"Source: {source_url}",
            HASHTAGS,
        ]
    ).strip() + "\n"


def _combined_score(rarity_score: float, viral_score: float, brand_fit: float) -> float:
    return (0.45 * rarity_score) + (0.35 * viral_score) + (0.20 * brand_fit)


def _candidate_for_record(record: dict, date_str: str) -> dict | None:
    if not str(record.get("created_at", "")).startswith(date_str):
        return None
    if not record.get("safety", {}).get("safe", False):
        return None
    if record.get("governance", {}).get("review_status") == "rejected":
        return None

    source_id = record.get("source_id")
    if not source_id:
        return None

    source = _load_json(_source_path(source_id))
    rarity = _load_json(_rarity_path(source_id))
    viral = _load_json(_viral_path(source_id))

    if not source or not rarity or not viral:
        print(f"[select] SKIP {source_id} missing source, rarity, or viral data")
        return None

    image_path = _find_image_path(source, date_str)
    if image_path is None:
        print(f"[select] SKIP {source_id} missing local image")
        return None

    rarity_score = float(rarity.get("rarity_score") or 0.0)
    viral_score = float(viral.get("viral_score") or 0.0)
    brand_fit = float(((viral.get("dimensions") or {}).get("brand_fit")) or 0.0)
    combined_score = _combined_score(rarity_score, viral_score, brand_fit)

    title = source.get("title") or record.get("record_id") or source_id
    caption = _build_caption(record)
    source_url = source.get("url") or source.get("image_url") or ""

    return {
        "record": record,
        "source": source,
        "rarity": rarity,
        "viral": viral,
        "image_path": image_path,
        "title": title,
        "caption": caption,
        "source_url": source_url,
        "rarity_score": round(rarity_score, 4),
        "viral_score": round(viral_score, 4),
        "brand_fit": round(brand_fit, 4),
        "combined_score": round(combined_score, 4),
    }


def _export_item(export_root: str, rank: int, item: dict) -> dict:
    slug = _slugify(item["title"])
    prefix = f"{rank:02d}-"
    for existing in os.listdir(export_root):
        if existing.startswith(prefix) and os.path.isdir(os.path.join(export_root, existing)):
            shutil.rmtree(os.path.join(export_root, existing))
    item_dir = os.path.join(export_root, f"{rank:02d}-{slug}")
    os.makedirs(item_dir, exist_ok=True)

    image_target = os.path.join(item_dir, "image.jpg")
    shutil.copyfile(item["image_path"], image_target)

    caption_path = os.path.join(item_dir, "caption.txt")
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(item["caption"].strip() + "\n")

    post_text = _post_text(item["title"], item["caption"], item["source_url"])
    post_path = os.path.join(item_dir, "post.txt")
    with open(post_path, "w", encoding="utf-8") as f:
        f.write(post_text)

    metadata = {
        "record_id": item["record"]["record_id"],
        "source_id": item["record"]["source_id"],
        "title": item["title"],
        "artist": item["source"].get("artist") or "",
        "source_url": item["source_url"],
        "created_at": item["record"].get("created_at"),
        "review_status": item["record"].get("governance", {}).get("review_status"),
        "safety_safe": item["record"].get("safety", {}).get("safe", False),
        "combined_score_formula": COMBINED_SCORE_FORMULA,
        "combined_score": item["combined_score"],
        "rarity_score": item["rarity_score"],
        "viral_score": item["viral_score"],
        "brand_fit": item["brand_fit"],
        "viral_recommended_use": item["viral"].get("recommended_use"),
        "caption": item["caption"],
        "post_path": post_path,
        "image_source_path": item["image_path"],
    }
    _save_json(os.path.join(item_dir, "metadata.json"), metadata)

    return {
        "rank": rank,
        "title": item["title"],
        "source_id": item["record"]["source_id"],
        "export_dir": item_dir,
        "combined_score": item["combined_score"],
        "post_path": post_path,
    }


def main(top: int, date_value: str) -> None:
    date_str = _resolve_date(date_value)
    candidates: list[dict] = []

    for path in _iter_record_paths():
        record = _load_json(path)
        if not record:
            continue
        candidate = _candidate_for_record(record, date_str)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["combined_score"],
            item["viral_score"],
            item["rarity_score"],
            item["title"].lower(),
        ),
        reverse=True,
    )
    selected = candidates[:top]

    export_root = os.path.join(SOCIAL_EXPORTS_DIR, date_str)
    os.makedirs(export_root, exist_ok=True)

    exported: list[dict] = []
    for index, item in enumerate(selected, start=1):
        result = _export_item(export_root, index, item)
        exported.append(result)
        print(
            f"[select] #{index} {result['source_id']} score={result['combined_score']:.4f} "
            f"-> {result['export_dir']}"
        )

    _save_json(
        os.path.join(export_root, "selected-items.json"),
        {
            "date": date_str,
            "top": top,
            "combined_score_formula": COMBINED_SCORE_FORMULA,
            "items": exported,
        },
    )

    print()
    print(f"[select] Exported {len(exported)} item(s) to {export_root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Select the best social-ready content bundles.")
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"Number of top items to export (default {DEFAULT_TOP})",
    )
    parser.add_argument(
        "--date",
        default="today",
        help="Date to select from, in YYYY-MM-DD format or 'today'",
    )
    args = parser.parse_args()
    if args.top <= 0:
        parser.error("--top must be greater than 0")
    main(top=args.top, date_value=args.date)
