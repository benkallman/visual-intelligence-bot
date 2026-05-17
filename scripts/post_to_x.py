#!/usr/bin/env python3
"""
Post one ranked social export to X (Twitter), with dry-run by default.

Behavior:
- Without --send: reads the post bundle, prints a preview, and exits. No
  network calls are made and no credentials are required.
- With --send: uploads the image via the v1.1 media endpoint, then creates
  the tweet via POST /2/tweets (X API v2). Credentials are loaded from the
  environment through src.utils.social_env and must never be printed.

Social export folders live under exports/social/<date>/ and are named
"<rank:02d>-<slug>/" (e.g. "01-some-artwork/"). Each folder must contain
post.txt (caption text) and image.jpg (the image to attach).

If you see a "client-not-enrolled" error from X, the app's access level on
developer.twitter.com does not include write permissions — check that the app
has "Read and Write" access (or higher) in the X developer portal.

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

# HTTP status codes that warrant a retry on media upload.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Seconds to sleep before each successive retry (3 retries → 4 total attempts).
_RETRY_DELAYS_SEC = (30, 90, 180)
# Images with either dimension above this are downsampled before upload.
_MAX_IMAGE_DIMENSION = 4096

SEND_REQUIRED_VARS = [
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_SECRET",
]


def _resolve_date(value: str) -> str:
    """Return an ISO date string, accepting 'today' as a shorthand."""
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _rank_folder(date_str: str, rank: int) -> Path:
    """Locate the export folder for a given date and rank.

    Folders are named "<rank:02d>-<slug>" so rank 1 matches any directory
    whose name starts with "01-". The first sorted match is returned when
    multiple slugs share the same rank prefix (shouldn't happen in practice).
    """
    base = Path(SOCIAL_EXPORTS_DIR) / date_str
    if not base.is_dir():
        raise FileNotFoundError(
            f"No social export folder for date {date_str} "
            f"(looked in {base}). "
            f"Use --date YYYY-MM-DD to specify an existing date, "
            f"or run export_public_digest.py + select_best_content.py to create one."
        )
    prefix = f"{rank:02d}-"
    matches = sorted(path for path in base.iterdir() if path.is_dir() and path.name.startswith(prefix))
    if not matches:
        raise FileNotFoundError(f"No social export folder found for rank {rank} on {date_str}")
    return matches[0]


def _read_post_bundle(date_str: str, rank: int) -> dict:
    """Load the post text and image path for a given date/rank pair.

    Returns a dict with keys: folder, post_path, image_path, text.
    Raises FileNotFoundError if the folder or required files are absent.
    """
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
    """Percent-encode a value per the OAuth 1.0a spec (RFC 3986 unreserved chars only)."""
    return quote(str(value), safe="~-._")


def _oauth_header(method: str, url: str, consumer_key: str, consumer_secret: str, token: str, token_secret: str) -> str:
    """Build an OAuth 1.0a Authorization header for a single request.

    Constructs a fresh nonce and timestamp per call so signatures are not
    reused. Never log the returned header — it embeds the signature derived
    from the consumer and token secrets.
    """
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    # OAuth 1.0a signature base string: METHOD&encoded_url&encoded_params
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
    """Post a tweet via the X API v2 endpoint (POST /2/tweets).

    v2 is required for tweet creation — the older v1.1 statuses/update
    endpoint is retired. The media_id must be obtained first from the v1.1
    media upload endpoint (see _send_post). A "client-not-enrolled" error
    here means the X app lacks write access; update it in the developer portal.
    """
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


VERIFY_CREDENTIALS_URL = "https://api.x.com/1.1/account/verify_credentials.json"


def _verify_credentials(credentials: dict[str, str]) -> None:
    """Optional diagnostic: confirm the four OAuth keys are mutually valid.

    Run with --verify-auth to call this before sending. Not part of the normal
    send path — the v1.1 verify_credentials endpoint sometimes returns 401 even
    when media upload and tweet creation succeed, which is misleading.

    - 200 → keys are correctly paired and the account is active.
    - 401 → consumer key/secret or access token/secret are wrong or mismatched;
            regenerate all four from the same X app.
    - 403 → keys are valid but the app lacks the required permission tier.
    """
    auth_header = _oauth_header(
        "GET",
        VERIFY_CREDENTIALS_URL,
        credentials["TWITTER_API_KEY"],
        credentials["TWITTER_API_SECRET"],
        credentials["TWITTER_ACCESS_TOKEN"],
        credentials["TWITTER_ACCESS_SECRET"],
    )
    resp = requests.get(
        VERIFY_CREDENTIALS_URL,
        headers={"Authorization": auth_header},
        params={"skip_status": "true", "include_entities": "false"},
        timeout=30,
    )
    print(f"[post-x] verify_credentials: HTTP {resp.status_code}")
    try:
        body = resp.json()
        # Print only non-sensitive fields — never print full token or secret values.
        safe = {k: body[k] for k in ("id_str", "screen_name", "errors") if k in body}
        print(json.dumps(safe, indent=2, ensure_ascii=False))
    except Exception:
        print(resp.text)


def _prepare_upload_image(image_path: Path) -> Path:
    """Return a resized JPEG copy if either dimension exceeds _MAX_IMAGE_DIMENSION.

    The original file is never modified. If Pillow is absent or resize fails,
    the original path is returned unchanged.
    """
    try:
        from PIL import Image  # optional — Pillow is already in requirements
    except ImportError:
        return image_path

    try:
        with Image.open(image_path) as img:
            orig_w, orig_h = img.size
        if orig_w <= _MAX_IMAGE_DIMENSION and orig_h <= _MAX_IMAGE_DIMENSION:
            return image_path
    except Exception as exc:
        print(f"[post-x] image size check failed ({exc}); using original")
        return image_path

    try:
        temp_dir = Path(ROOT_DIR) / "temp" / "post_media"
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / image_path.name
        with Image.open(image_path) as img:
            img.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            new_size = img.size
            img.save(out_path, "JPEG", quality=92)
        print(
            f"[post-x] image normalized: {orig_w}x{orig_h} → {new_size[0]}x{new_size[1]}"
            f" ({out_path.stat().st_size} bytes) → {out_path}"
        )
        return out_path
    except Exception as exc:
        print(f"[post-x] image normalization failed ({exc}); using original")
        return image_path


def _upload_media(image_path: Path, credentials: dict[str, str]) -> str:
    """Upload image to the X v1.1 media endpoint with retry on transient errors.

    Retries on HTTP 429/500/502/503/504 with progressive delays defined by
    _RETRY_DELAYS_SEC (3 retries = 4 total attempts). Raises RuntimeError on
    permanent failures or after all retries are exhausted.
    """
    upload_path = _prepare_upload_image(image_path)
    mime_type = mimetypes.guess_type(upload_path.name)[0] or "image/jpeg"
    delays = list(_RETRY_DELAYS_SEC)
    total_attempts = len(delays) + 1
    last_response: requests.Response | None = None

    for attempt in range(1, total_attempts + 1):
        auth_header = _oauth_header(
            "POST", UPLOAD_MEDIA_URL,
            credentials["TWITTER_API_KEY"], credentials["TWITTER_API_SECRET"],
            credentials["TWITTER_ACCESS_TOKEN"], credentials["TWITTER_ACCESS_SECRET"],
        )
        try:
            with open(upload_path, "rb") as f:
                resp = requests.post(
                    UPLOAD_MEDIA_URL,
                    headers={"Authorization": auth_header},
                    files={"media": (upload_path.name, f, mime_type)},
                    timeout=60,
                )
        except requests.exceptions.RequestException as exc:
            # Network-level failure — treat as retryable.
            print(f"[post-x] media upload attempt {attempt}: network error: {exc}")
            if attempt <= len(delays):
                delay = delays[attempt - 1]
                print(f"[post-x] retrying in {delay}s …")
                time.sleep(delay)
            continue

        last_response = resp
        print(f"[post-x] media upload attempt {attempt}: HTTP {resp.status_code}")

        if resp.status_code == 200:
            try:
                body = resp.json()
                print(json.dumps(body, indent=2, ensure_ascii=False))
                media_id = body.get("media_id_string")
            except Exception:
                print(resp.text)
                media_id = None
            if media_id:
                return media_id
            raise RuntimeError(
                f"X media upload returned HTTP 200 but no media_id_string. "
                f"image={upload_path}  response={resp.text[:500]}"
            )

        if resp.status_code not in _RETRY_STATUSES:
            try:
                body_text = json.dumps(resp.json(), indent=2, ensure_ascii=False)
            except Exception:
                body_text = resp.text
            raise RuntimeError(
                f"X media upload failed (non-retryable). "
                f"image={upload_path}  HTTP {resp.status_code}  {body_text[:500]}"
            )

        # Retryable status code.
        if attempt <= len(delays):
            delay = delays[attempt - 1]
            print(f"[post-x] media upload error (HTTP {resp.status_code}); retrying in {delay}s …")
            time.sleep(delay)

    # All attempts exhausted.
    if last_response is not None:
        try:
            tail = json.dumps(last_response.json(), indent=2, ensure_ascii=False)
        except Exception:
            tail = last_response.text
        status = last_response.status_code
    else:
        tail, status = "(no response — network error on all attempts)", "N/A"
    raise RuntimeError(
        f"X media upload failed after {total_attempts} attempts. "
        f"image={upload_path}  HTTP {status}  {tail[:500]}"
    )


def _send_post(bundle: dict, credentials: dict[str, str]) -> dict:
    """Upload the image then create the tweet, returning the API response body.

    Raises RuntimeError on any failure so callers do not need to handle
    requests.HTTPError separately.
    """
    media_id = _upload_media(bundle["image_path"], credentials)

    tweet_response = _create_tweet_v2(bundle["text"], media_id, credentials)
    print(f"[post-x] Response: HTTP {tweet_response.status_code}")
    try:
        response_body = tweet_response.json()
    except Exception:
        response_body = {"raw": tweet_response.text}
    print(json.dumps(response_body, indent=2, ensure_ascii=False))
    try:
        tweet_response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"X tweet creation failed: HTTP {tweet_response.status_code}  "
            f"{tweet_response.text[:500]}"
        ) from exc
    return response_body


def main(date_value: str, rank: int, send: bool, verify_auth: bool = False) -> None:
    """Resolve the export bundle, preview it, and optionally post to X.

    In dry-run mode (send=False) no credentials are loaded and no network
    calls are made. In send mode, credentials are pulled from the environment
    via load_social_env — values are never echoed to stdout.

    verify_auth=True runs _verify_credentials before sending (opt-in via
    --verify-auth). It is not part of the normal send path to avoid spurious
    401 noise from the v1.1 endpoint when the post itself would succeed.
    """
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

    # Credentials are loaded here (not at import time) so dry runs never
    # require them to be present in the environment.
    resolved = load_social_env(ROOT_DIR)
    missing = [key for key in SEND_REQUIRED_VARS if not resolved[key]["present"]]
    if missing:
        raise RuntimeError(f"Missing required X credentials for send mode: {', '.join(missing)}")

    credentials = {key: str(resolved[key]["value"]) for key in SEND_REQUIRED_VARS}
    if verify_auth:
        _verify_credentials(credentials)
    _send_post(bundle, credentials)
    print("[post-x] Sent successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dry-run or send one selected social export to X.")
    parser.add_argument("--date", default="today", help="Date folder in exports/social, or 'today'")
    parser.add_argument("--rank", type=int, required=True, help="Rank number to post from the social export folder")
    parser.add_argument("--send", action="store_true", help="Actually send the post to X")
    parser.add_argument(
        "--verify-auth",
        action="store_true",
        help="Run verify_credentials diagnostic before sending (optional; may return 401 even on success)",
    )
    args = parser.parse_args()
    if args.rank <= 0:
        parser.error("--rank must be greater than 0")
    try:
        main(date_value=args.date, rank=args.rank, send=args.send, verify_auth=args.verify_auth)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[post-x] Error: {exc}", file=sys.stderr)
        sys.exit(1)
