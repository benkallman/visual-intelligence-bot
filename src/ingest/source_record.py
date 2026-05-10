import json
import os
import hashlib
import datetime
import io
import httpx
from PIL import Image

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_JPEG_QUALITY = 75
_IMAGE_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}

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
    max_side = int(os.getenv("OLLAMA_MAX_IMAGE_SIZE", "512"))
    filename = f"{source_id}.jpg"
    path = os.path.join(images_dir, filename)
    response = httpx.get(url, headers=_IMAGE_HEADERS, follow_redirects=True, timeout=30)
    response.raise_for_status()
    raw_bytes = response.content

    effective_max_side = max_side
    if len(raw_bytes) > _MAX_IMAGE_BYTES:
        effective_max_side = min(max_side, 384)

    image = Image.open(io.BytesIO(raw_bytes))
    if image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    if max(width, height) > effective_max_side:
        scale = effective_max_side / max(width, height)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    processed_bytes = buf.getvalue()

    with open(path, "wb") as f:
        f.write(processed_bytes)

    checksum = hashlib.sha256(processed_bytes).hexdigest()
    return f"data/images/{filename}", checksum
