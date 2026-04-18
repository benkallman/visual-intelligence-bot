import json
import os
import hashlib
import datetime
import httpx

SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sources")


def create_source_record(
    source_id: str,
    url: str,
    image_url: str = None,
    title: str = None,
    artist: str = None,
    download_image: bool = False,
) -> dict:
    record = {
        "source_id": source_id,
        "url": url,
        "image_url": image_url,
        "local_image_path": None,
        "source_type": "manual_entry",
        "approved_source": True,
        "approved_source_id": None,
        "title": title,
        "artist": artist,
        "date_created": None,
        "medium": None,
        "dimensions": None,
        "collection": None,
        "access_date": datetime.date.today().isoformat(),
        "rights_flag": "rights_unknown",
        "rights_notes": None,
        "duplicate_of": None,
        "checksum": None,
    }

    if download_image and image_url:
        local_path, checksum = _download_image(image_url, source_id)
        record["local_image_path"] = local_path
        record["checksum"] = checksum

    return record


def save_source_record(record: dict) -> str:
    os.makedirs(SOURCES_DIR, exist_ok=True)
    path = os.path.join(SOURCES_DIR, f"{record['source_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def _download_image(url: str, source_id: str) -> tuple[str, str]:
    images_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "images")
    os.makedirs(images_dir, exist_ok=True)
    ext = url.rsplit(".", 1)[-1].split("?")[0] if "." in url.rsplit("/", 1)[-1] else "jpg"
    filename = f"{source_id}.{ext}"
    path = os.path.join(images_dir, filename)
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    with open(path, "wb") as f:
        f.write(response.content)
    checksum = hashlib.sha256(response.content).hexdigest()
    return f"data/images/{filename}", checksum
