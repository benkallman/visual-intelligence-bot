#!/usr/bin/env python3
"""
Dry-run by default for posting one selected social export to X.

Usage:
    python scripts/post_to_x.py --date today --rank 1
    python scripts/post_to_x.py --date today --rank 1 --send
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.social_env import load_social_env

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
SOCIAL_EXPORTS_DIR = os.path.join(ROOT_DIR, "exports", "social")
UPLOAD_MEDIA_URL = "https://upload.twitter.com/1.1/media/upload.json"
CREATE_POST_URL = "https://api.x.com/2/tweets"
MAX_POST_CHARS = 280
SEND_REQUIRED_VARS = [
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_SECRET",
]


def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _rank_folder(date_str: str, rank: int) -> Path:
    base = Path(SOCIAL_EXPORTS_DIR) / date_str
    prefix = f"{rank:02d}-"
    matches = sorted(path for path in base.iterdir() if path.is_dir() and path.name.startswith(prefix))
    if not matches:
        raise FileNotFoundError(f"No social export folder found for rank {rank} on {date_str}")
    return matches[0]


def _read_post_bundle(date_str: str, rank: int) -> dict:
    folder = _rank_folder(date_str, rank)
    post_path = folder / "post.txt"
    image_path = folder / "image.jpg"

    if not post_path.is_file():
        raise FileNotFoundError(f"Missing post.txt in {folder}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Missing image.jpg in {folder}")

    text = post_path.read_text(encoding="utf-8").strip()
    return {
        "folder": folder,
        "post_path": post_path,
        "image_path": image_path,
        "text": text,
    }


def _oauth_percent_encode(value: str) -> str:
    return quote(str(value), safe="~-._")


def _oauth_header(method: str, url: str, consumer_key: str, consumer_secret: str, token: str, token_secret: str) -> str:
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    normalized = "&".join(
        f"{_oauth_percent_encode(key)}={_oauth_percent_encode(value)}"
        for key, value in sorted(oauth_params.items())
    )
    base_string = "&".join(
        [
            method.upper(),
            _oauth_percent_encode(url),
            _oauth_percent_encode(normalized),
        ]
    )
    signing_key = f"{_oauth_percent_encode(consumer_secret)}&{_oauth_percent_encode(token_secret)}"
    digest = hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")

    return "OAuth " + ", ".join(
        f'{_oauth_percent_encode(key)}="{_oauth_percent_encode(value)}"'
        for key, value in sorted(oauth_params.items())
    )


def _create_tweet_v2(text: str, media_id: str, credentials: dict[str, str]) -> requests.Response:
    auth_header = _oauth_header(
        "POST",
        CREATE_POST_URL,
        credentials["TWITTER_API_KEY"],
        credentials["TWITTER_API_SECRET"],
        credentials["TWITTER_ACCESS_TOKEN"],
        credentials["TWITTER_ACCESS_SECRET"],
    )
    body = json.dumps({"text": text, "media": {"media_ids": [media_id]}})
    return requests.post(
        CREATE_POST_URL,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
        },
        data=body,
        timeout=60,
    )


def _send_post(bundle: dict, credentials: dict[str, str]) -> dict:
    auth_header = _oauth_header(
        "POST",
        UPLOAD_MEDIA_URL,
        credentials["TWITTER_API_KEY"],
        credentials["TWITTER_API_SECRET"],
        credentials["TWITTER_ACCESS_TOKEN"],
        credentials["TWITTER_ACCESS_SECRET"],
    )

    mime_type = mimetypes.guess_type(bundle["image_path"].name)[0] or "image/jpeg"
    with open(bundle["image_path"], "rb") as f:
        files = {"media": (bundle["image_path"].name, f, mime_type)}
        upload_response = requests.post(
            UPLOAD_MEDIA_URL,
            headers={"Authorization": auth_header},
            files=files,
            timeout=60,
        )
    upload_response.raise_for_status()
    media_id = upload_response.json().get("media_id_string")
    if not media_id:
        raise RuntimeError("X media upload did not return media_id_string")

    tweet_response = _create_tweet_v2(bundle["text"], media_id, credentials)
    print(f"[post-x] Response: HTTP {tweet_response.status_code}")
    try:
        response_body = tweet_response.json()
    except Exception:
        response_body = {"raw": tweet_response.text}
    print(json.dumps(response_body, indent=2, ensure_ascii=False))
    tweet_response.raise_for_status()
    return response_body


def main(date_value: str, rank: int, send: bool) -> None:
    date_str = _resolve_date(date_value)
    bundle = _read_post_bundle(date_str, rank)

    char_count = len(bundle["text"])
    within_limit = char_count <= MAX_POST_CHARS
    limit_note = f"[within {MAX_POST_CHARS}]" if within_limit else f"[EXCEEDS limit by {char_count - MAX_POST_CHARS}]"

    print(f"[post-x] Folder: {bundle['folder']}")
    print(f"[post-x] Image: {bundle['image_path'].name} ({bundle['image_path'].stat().st_size} bytes)")
    print(f"[post-x] Characters: {char_count} {limit_note}")
    print("[post-x] Preview:")
    print()
    print(bundle["text"])
    print()

    if not send:
        print("[post-x] Dry run only. Use --send to post to X.")
        return

    resolved = load_social_env(ROOT_DIR)
    missing = [key for key in SEND_REQUIRED_VARS if not resolved[key]["present"]]
    if missing:
        raise RuntimeError(f"Missing required X credentials for send mode: {', '.join(missing)}")

    credentials = {key: str(resolved[key]["value"]) for key in SEND_REQUIRED_VARS}
    _send_post(bundle, credentials)
    print("[post-x] Sent successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dry-run or send one selected social export to X.")
    parser.add_argument("--date", default="today", help="Date folder in exports/social, or 'today'")
    parser.add_argument("--rank", type=int, required=True, help="Rank number to post from the social export folder")
    parser.add_argument("--send", action="store_true", help="Actually send the post to X")
    args = parser.parse_args()
    if args.rank <= 0:
        parser.error("--rank must be greater than 0")
    main(date_value=args.date, rank=args.rank, send=args.send)
