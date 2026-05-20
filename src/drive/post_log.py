"""Google Drive + Google Docs post logging for successful social posts."""

from __future__ import annotations

import json
import mimetypes
import os
import pathlib

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]

_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

_ENV_DOC_ID = "IMAGEBOT_DRIVE_LOG_DOC_ID"
_ENV_FOLDER_ID = "IMAGEBOT_DRIVE_LOG_FOLDER_ID"
_ENV_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"

_ROW_FIELDS = [
    "posted_at",
    "pack",
    "artwork_title",
    "artist/maker",
    "source_url",
    "local_image_path",
    "google_drive_image_url",
    "social_post_url/status_id",
    "caption excerpt",
    "queue folder",
    "notes",
]


def _read_dotenv(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def _get_env_value(key: str, dotenv_values: dict[str, str]) -> str:
    value = os.environ.get(key)
    if value:
        return value.strip()
    return dotenv_values.get(key, "").strip()


def _load_config(root_dir: pathlib.Path) -> dict[str, str] | None:
    dotenv_values = _read_dotenv(root_dir / ".env")
    doc_id = _get_env_value(_ENV_DOC_ID, dotenv_values)
    folder_id = _get_env_value(_ENV_FOLDER_ID, dotenv_values)
    credentials_path = _get_env_value(_ENV_CREDENTIALS, dotenv_values)

    if not credentials_path or not doc_id:
        print("[drive_log] disabled missing GOOGLE_APPLICATION_CREDENTIALS or IMAGEBOT_DRIVE_LOG_DOC_ID")
        return None
    if not folder_id:
        print("[drive_log] failed upload/log append err=missing IMAGEBOT_DRIVE_LOG_FOLDER_ID")
        return None
    return {
        "doc_id": doc_id,
        "folder_id": folder_id,
        "credentials_path": credentials_path,
    }


def _build_services(credentials_path: str):
    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=_SCOPES,
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    return drive, docs


def _read_folder_metadata(folder: pathlib.Path) -> dict:
    meta_path = folder / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tweet_url(entry: dict) -> str:
    tweet_id = str(entry.get("tweet_id") or "").strip()
    if tweet_id:
        return f"https://x.com/i/web/status/{tweet_id}"
    return str(entry.get("social_post_url") or entry.get("status_id") or "").strip()


def _excerpt(text: str, limit: int = 180) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _clean_cell(value: object) -> str:
    text = str(value or "")
    text = text.replace("\t", " ")
    text = text.replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _slugify(value: str) -> str:
    result = []
    for ch in (value or "").lower():
        if ch.isalnum():
            result.append(ch)
        elif result and result[-1] != "-":
            result.append("-")
    slug = "".join(result).strip("-")
    return slug or "untitled"


def _drive_filename(entry: dict, meta: dict, image_path: pathlib.Path) -> str:
    date_str = str(entry.get("date") or "").strip() or "undated"
    rank = str(entry.get("rank") or "").strip() or "00"
    pack_id = str(entry.get("pack_id") or meta.get("pack_id") or "unknown").strip()
    tweet_id = str(entry.get("tweet_id") or "").strip()
    title = str(meta.get("title") or image_path.stem or "untitled").strip()
    ext = image_path.suffix.lower() or ".jpg"
    parts = [
        date_str,
        str(rank).zfill(2),
        _slugify(pack_id),
        _slugify(title)[:60],
    ]
    if tweet_id:
        parts.append(tweet_id)
    return "_".join(part for part in parts if part) + ext


def _upload_image(drive_service, image_path: pathlib.Path, folder_id: str, filename: str) -> str:
    media = MediaFileUpload(
        str(image_path),
        mimetype=mimetypes.guess_type(str(image_path))[0] or "application/octet-stream",
        resumable=False,
    )
    uploaded = (
        drive_service.files()
        .create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id,webViewLink,webContentLink",
        )
        .execute()
    )
    file_id = uploaded.get("id")
    if file_id:
        try:
            drive_service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
            uploaded = (
                drive_service.files()
                .get(fileId=file_id, fields="id,webViewLink,webContentLink")
                .execute()
            )
        except Exception:
            pass

    return (
        uploaded.get("webViewLink")
        or uploaded.get("webContentLink")
        or (f"https://drive.google.com/file/d/{file_id}/view" if file_id else "")
    )


def _document_text(doc: dict) -> str:
    parts: list[str] = []
    for block in doc.get("body", {}).get("content", []):
        paragraph = block.get("paragraph") or {}
        for element in paragraph.get("elements", []):
            text_run = element.get("textRun") or {}
            content = text_run.get("content")
            if content:
                parts.append(content)
    return "".join(parts)


def _end_of_doc_index(doc: dict) -> int:
    content = doc.get("body", {}).get("content", [])
    if not content:
        return 1
    return max(1, int(content[-1].get("endIndex", 1)) - 1)


def _format_row(row: dict[str, str]) -> str:
    return "\t".join(_clean_cell(row.get(field, "")) for field in _ROW_FIELDS) + "\n"


def _append_row_to_doc(docs_service, doc_id: str, row: dict[str, str]) -> None:
    doc = docs_service.documents().get(documentId=doc_id).execute()
    doc_text = _document_text(doc)
    header_row = "\t".join(_ROW_FIELDS) + "\n"
    row_text = _format_row(row)
    needs_header = "google_drive_image_url" not in doc_text

    prefix = ""
    if doc_text and not doc_text.endswith("\n"):
        prefix = "\n"

    payload = prefix + (header_row if needs_header else "") + row_text

    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": _end_of_doc_index(doc)},
                        "text": payload,
                    }
                }
            ]
        },
    ).execute()


def build_post_row(
    entry: dict,
    folder: pathlib.Path,
    image_path: str | pathlib.Path,
    post_text: str,
    google_drive_image_url: str = "",
    notes: str | None = None,
) -> dict[str, str]:
    folder = pathlib.Path(folder).resolve()
    image_path = pathlib.Path(image_path).resolve()
    meta = _read_folder_metadata(folder)

    title = str(meta.get("title") or "").strip()
    artist = str(meta.get("artist") or meta.get("maker") or "").strip()
    source_url = str(meta.get("source_url") or meta.get("page_url") or "").strip()
    queue_image = (folder / "image.jpg").resolve()

    note_parts: list[str] = []
    if notes:
        note_parts.append(notes)
    else:
        note_parts.append("posted successfully")
    if image_path != queue_image:
        note_parts.append(f"posted exact upload file differs from queue image: {image_path.name}")

    return {
        "posted_at": str(entry.get("posted_at") or "").strip(),
        "pack": str(entry.get("pack_id") or meta.get("pack_id") or "").strip(),
        "artwork_title": title,
        "artist/maker": artist,
        "source_url": source_url,
        "local_image_path": str(image_path),
        "google_drive_image_url": google_drive_image_url,
        "social_post_url/status_id": _tweet_url(entry),
        "caption excerpt": _excerpt(post_text),
        "queue folder": str(folder),
        "notes": "; ".join(part for part in note_parts if part),
    }


def log_post(
    entry: dict,
    folder: pathlib.Path,
    image_path: str | pathlib.Path,
    post_text: str,
    *,
    root_dir: pathlib.Path | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    folder = pathlib.Path(folder).resolve()
    image_path = pathlib.Path(image_path).resolve()
    root_dir = pathlib.Path(root_dir or ROOT_DIR).resolve()

    result: dict[str, object] = {
        "status": "pending",
        "row": build_post_row(entry, folder, image_path, post_text),
        "drive_url": "",
        "image_path": str(image_path),
    }

    if dry_run:
        result["status"] = "dry_run"
        return result

    if not _GOOGLE_AVAILABLE:
        print("[drive_log] failed upload/log append err=google-api-python-client not installed")
        result["status"] = "error"
        return result

    config = _load_config(root_dir)
    if config is None:
        result["status"] = "disabled"
        return result

    credentials_path = pathlib.Path(config["credentials_path"]).expanduser()
    if not credentials_path.is_file():
        print("[drive_log] disabled missing GOOGLE_APPLICATION_CREDENTIALS or IMAGEBOT_DRIVE_LOG_DOC_ID")
        result["status"] = "disabled"
        return result

    try:
        drive_service, docs_service = _build_services(str(credentials_path))
    except Exception as exc:
        print(f"[drive_log] failed upload/log append err={exc}")
        result["status"] = "error"
        return result

    notes = "posted successfully"
    drive_url = ""
    try:
        drive_url = _upload_image(
            drive_service=drive_service,
            image_path=image_path,
            folder_id=config["folder_id"],
            filename=_drive_filename(entry, _read_folder_metadata(folder), image_path),
        )
        result["drive_url"] = drive_url
    except Exception as exc:
        notes = f"drive upload failed: {exc}"
        print(f"[drive_log] failed upload/log append err={exc}")

    row = build_post_row(
        entry=entry,
        folder=folder,
        image_path=image_path,
        post_text=post_text,
        google_drive_image_url=drive_url,
        notes=notes,
    )
    result["row"] = row

    try:
        _append_row_to_doc(docs_service, config["doc_id"], row)
    except Exception as exc:
        print(f"[drive_log] failed upload/log append err={exc}")
        result["status"] = "error"
        return result

    result["status"] = "logged"
    return result
