"""Optional Google Drive + Docs posting log.

Disabled unless IMAGEBOT_DRIVE_LOG_ENABLED=1.
All failures are non-fatal — warnings are printed and posting continues.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2 import service_account
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

if TYPE_CHECKING:
    pass

_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

_ENV_ENABLED = "IMAGEBOT_DRIVE_LOG_ENABLED"
_ENV_DOC_ID = "IMAGEBOT_DRIVE_LOG_DOC_ID"
_ENV_FOLDER_ID = "IMAGEBOT_DRIVE_LOG_FOLDER_ID"
_ENV_OVERSIZE_DOC_ID = "IMAGEBOT_OVERSIZE_LOG_DOC_ID"
_ENV_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"


def _is_enabled() -> bool:
    return os.environ.get(_ENV_ENABLED, "").strip() == "1"


def _read_env() -> tuple[str, str, str] | None:
    """Returns (doc_id, folder_id, credentials_path) or None if any are missing."""
    doc_id = os.environ.get(_ENV_DOC_ID, "").strip()
    folder_id = os.environ.get(_ENV_FOLDER_ID, "").strip()
    creds_path = os.environ.get(_ENV_CREDENTIALS, "").strip()
    if not doc_id or not folder_id or not creds_path:
        return None
    return doc_id, folder_id, creds_path


def _build_services(credentials_path: str):
    """Returns (drive_service, docs_service) using a service account JSON file."""
    creds = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=_SCOPES
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    return drive, docs


def _make_filename(date_str: str, rank: int | str, pack_id: str | None) -> str:
    pack_slug = (pack_id or "unknown").replace(" ", "_")
    return f"{date_str}_{str(rank).zfill(2)}_{pack_slug}.jpg"


def _upload_image(drive_service, image_path: str, folder_id: str, filename: str) -> str | None:
    """Uploads image to Drive folder; returns webViewLink or None."""
    media = MediaFileUpload(image_path, mimetype="image/jpeg", resumable=False)
    file_meta = {"name": filename, "parents": [folder_id]}
    uploaded = (
        drive_service.files()
        .create(body=file_meta, media_body=media, fields="id,webViewLink")
        .execute()
    )
    return uploaded.get("webViewLink")


def _end_of_doc_index(docs_service, doc_id: str) -> int:
    """Returns the index just before the final newline in the document body."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body = doc.get("body", {})
    content = body.get("content", [])
    if not content:
        return 1
    last = content[-1]
    end_index = last.get("endIndex", 1)
    return max(1, end_index - 1)


def _append_to_doc_end(docs_service, doc_id: str, text: str) -> None:
    """Appends text at the actual end of the document body."""
    index = _end_of_doc_index(docs_service, doc_id)
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": index, "segmentId": ""},
                        "text": text,
                    }
                }
            ]
        },
    ).execute()


def _read_folder_metadata(folder: pathlib.Path) -> dict:
    meta_path = folder / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        import json
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_log_block(
    entry: dict,
    meta: dict,
    post_text: str,
    image_link: str | None,
) -> str:
    """Formats the log entry as a text block to append to the Google Doc."""
    tweet_id = entry.get("tweet_id", "")
    posted_at = entry.get("posted_at", "")
    pack_id = entry.get("pack_id") or meta.get("pack_id", "")
    folder = entry.get("folder", "")

    title = meta.get("title", "")
    artist = meta.get("artist", "") or meta.get("maker", "")
    year = meta.get("year", "") or meta.get("date", "")
    source_url = meta.get("source_url", "") or meta.get("page_url", "")

    tweet_url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "(no tweet id)"
    image_line = image_link if image_link else "(upload failed)"

    lines = [
        "\n---\n",
        f"Posted at: {posted_at}\n",
        f"Pack: {pack_id}\n",
        f"Title: {title}\n",
        f"Artist: {artist}\n",
        f"Year: {year}\n",
        f"Caption: {post_text}\n",
        f"X post: {tweet_url}\n",
        f"Original source: {source_url}\n",
        f"Drive image: {image_line}\n",
        f"Queue folder: {folder}\n",
        f"Tweet ID: {tweet_id}\n",
        "Notes: posted successfully\n",
    ]
    return "".join(lines)


def _get_image_dims(image_path: str) -> "tuple[int, int] | None":
    """Return (width, height) using Pillow if available; otherwise None."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return None


def _build_oversize_block(
    entry: dict,
    meta: dict,
    image_path: str,
    error_text: str,
    original_dims: "tuple[int, int] | None",
    normalized_path: "str | None",
    normalized_size: "int | None",
    posted_later_url: "str | None",
) -> str:
    detected_at = entry.get("failed_at") or entry.get("posted_at") or ""
    pack_id = entry.get("pack_id") or meta.get("pack_id", "")
    date_str = entry.get("date", "")
    rank = entry.get("rank", "")
    title = meta.get("title", "")
    artist = meta.get("artist", "") or meta.get("maker", "")
    year = meta.get("year", "") or meta.get("date", "")
    source_url = meta.get("source_url", "") or meta.get("page_url", "")

    try:
        orig_size = pathlib.Path(image_path).stat().st_size
    except Exception:
        orig_size = ""

    dims_str = f"{original_dims[0]}x{original_dims[1]}" if original_dims else "(unknown)"

    lines = [
        "\n---\n",
        f"Detected at: {detected_at}\n",
        f"Pack: {pack_id}  |  Date: {date_str}  |  Rank: {rank}\n",
        f"Title: {title}\n",
        f"Artist: {artist}\n",
        f"Year: {year}\n",
        f"Original source: {source_url}\n",
        f"Local image path: {image_path}\n",
        f"Original file size (bytes): {orig_size}\n",
        f"Original dimensions: {dims_str}\n",
        f"X upload error: {error_text}\n",
        f"Normalized path: {normalized_path or '(none)'}\n",
        f"Normalized size (bytes): {normalized_size if normalized_size is not None else '(none)'}\n",
        f"Posted later URL: {posted_later_url or '(not posted)'}\n",
        f"Notes: oversized/compressed image logged\n",
    ]
    return "".join(lines)


def log_post(
    entry: dict,
    folder: "pathlib.Path",
    image_path: str,
    post_text: str,
) -> None:
    """Main entry point. Fully non-fatal — all errors are caught and printed."""
    if not _is_enabled():
        return

    if not _GOOGLE_AVAILABLE:
        print("[drive-log] google-api-python-client not installed; skipping Drive log")
        return

    env = _read_env()
    if env is None:
        print("[drive-log] disabled/missing credentials")
        return

    doc_id, folder_id, creds_path = env

    if not pathlib.Path(creds_path).is_file():
        print("[drive-log] disabled/missing credentials")
        return

    try:
        drive_service, docs_service = _build_services(creds_path)
    except Exception as exc:
        print(f"[drive-log] failed to build Google services: {exc}")
        return

    meta = _read_folder_metadata(folder)

    image_link: str | None = None
    try:
        date_str = entry.get("date", "")
        rank = entry.get("rank", 0)
        pack_id = entry.get("pack_id") or meta.get("pack_id")
        filename = _make_filename(date_str, rank, pack_id)
        image_link = _upload_image(drive_service, image_path, folder_id, filename)
        print(f"[drive-log] image uploaded: {image_link}")
    except Exception as exc:
        print(f"[drive-log] image upload failed: {exc}")

    try:
        block = _build_log_block(entry, meta, post_text, image_link)
        _append_to_doc_end(docs_service, doc_id, block)
        print(f"[drive-log] doc updated: https://docs.google.com/document/d/{doc_id}/edit")
    except Exception as exc:
        print(f"[drive-log] doc append failed: {exc}")


def log_oversized_image(
    entry: dict,
    folder: "pathlib.Path",
    image_path: str,
    error_text: str,
    normalized_path: "str | None" = None,
    normalized_size: "int | None" = None,
    posted_later_url: "str | None" = None,
    original_dims: "tuple[int, int] | None" = None,
) -> None:
    """Append an oversized/compressed image record to IMAGEBOT_OVERSIZE_LOG_DOC_ID.

    Fully non-fatal — all errors are caught and printed. Disabled unless
    IMAGEBOT_DRIVE_LOG_ENABLED=1 and IMAGEBOT_OVERSIZE_LOG_DOC_ID is set.
    """
    if not _is_enabled():
        return

    if not _GOOGLE_AVAILABLE:
        print("[oversize-log] google-api-python-client not installed; skipping")
        return

    oversize_doc_id = os.environ.get(_ENV_OVERSIZE_DOC_ID, "").strip()
    if not oversize_doc_id:
        print("[oversize-log] IMAGEBOT_OVERSIZE_LOG_DOC_ID not set; skipping")
        return

    creds_path = os.environ.get(_ENV_CREDENTIALS, "").strip()
    if not creds_path or not pathlib.Path(creds_path).is_file():
        print("[oversize-log] disabled/missing credentials")
        return

    try:
        _, docs_service = _build_services(creds_path)
    except Exception as exc:
        print(f"[oversize-log] failed to build Google services: {exc}")
        return

    meta = _read_folder_metadata(folder)

    # Try to read original dims from Pillow if caller didn't supply them.
    if original_dims is None:
        original_dims = _get_image_dims(image_path)

    try:
        block = _build_oversize_block(
            entry=entry,
            meta=meta,
            image_path=image_path,
            error_text=error_text,
            original_dims=original_dims,
            normalized_path=normalized_path,
            normalized_size=normalized_size,
            posted_later_url=posted_later_url,
        )
        _append_to_doc_end(docs_service, oversize_doc_id, block)
        print(f"[oversize-log] doc updated: https://docs.google.com/document/d/{oversize_doc_id}/edit")
    except Exception as exc:
        print(f"[oversize-log] doc append failed: {exc}")
