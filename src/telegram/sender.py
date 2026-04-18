"""
Optional Telegram channel sender.

Controlled entirely by env vars. Does nothing if TELEGRAM_ENABLED != "true".
Never sends safety-rejected or safety-uncertain items.
Never sends items below TELEGRAM_MIN_RARITY_SCORE.

Image sending: attempts sendPhoto (URL) first; falls back to sendMessage
with the URL in the message body if Telegram cannot resolve the image.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_BLOCKED_STATUSES = {"safety_rejected", "safety_uncertain"}
_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_DESC_CHARS = 280
_MAX_CAPTION = 1024
_MAX_MESSAGE = 4096


def is_enabled() -> bool:
    return os.getenv("TELEGRAM_ENABLED", "").strip().lower() == "true"


def send_if_eligible(
    interpretation_record: dict,
    rarity_record: dict,
    source_record: dict,
) -> bool:
    """Send to Telegram if all conditions are met. Returns True if sent."""
    if not is_enabled():
        return False

    # Hard block: safety gate
    gov_status = interpretation_record.get("governance", {}).get("review_status", "")
    if gov_status in _BLOCKED_STATUSES:
        logger.info("Telegram: skipped — safety status: %s", gov_status)
        return False

    # Rarity threshold
    rarity_score = rarity_record.get("rarity_score", 0.0)
    min_score = _min_rarity_score()
    if rarity_score < min_score:
        logger.info(
            "Telegram: skipped — score %.2f below threshold %.2f",
            rarity_score,
            min_score,
        )
        return False

    # Credentials
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        logger.warning(
            "Telegram: TELEGRAM_ENABLED=true but BOT_TOKEN or CHANNEL_ID not set — skipping"
        )
        return False

    text = _build_message(interpretation_record, rarity_record, source_record)
    image_url = source_record.get("image_url") or source_record.get("url")

    return _send(token, channel_id, text, image_url)


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _build_message(
    interpretation_record: dict,
    rarity_record: dict,
    source_record: dict,
) -> str:
    record_id = interpretation_record.get("record_id", "")
    title = source_record.get("title") or record_id
    artist = source_record.get("artist") or "Unknown"
    date_created = source_record.get("date_created") or "Unknown"
    source_url = source_record.get("url", "")

    description = interpretation_record.get("pass1", {}).get("description", "")
    description = _truncate(description, _MAX_DESC_CHARS)

    rarity_score = rarity_record.get("rarity_score", 0.0)
    reason = rarity_record.get("reason", "")
    key_elements = rarity_record.get("key_elements", [])[:3]
    elements_str = " · ".join(key_elements) if key_elements else "—"

    lines = [
        f"<b>{_esc(title)}</b>",
        f"{_esc(artist)} · {_esc(str(date_created))}",
        "",
        _esc(description),
        "",
        f"Rarity: {rarity_score:.2f} — {_esc(reason)}",
        f"Elements: {_esc(elements_str)}",
    ]

    if source_url:
        lines += ["", source_url]

    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 0 else cut) + "..."


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _send(token: str, channel_id: str, text: str, image_url: str | None) -> bool:
    if image_url:
        if _send_photo(token, channel_id, image_url, caption=text):
            return True
        logger.info("Telegram: sendPhoto failed — falling back to sendMessage")
    return _send_message(token, channel_id, text)


def _send_photo(token: str, channel_id: str, photo_url: str, caption: str) -> bool:
    url = _TELEGRAM_API.format(token=token, method="sendPhoto")
    try:
        r = requests.post(
            url,
            json={
                "chat_id": channel_id,
                "photo": photo_url,
                "caption": caption[:_MAX_CAPTION],
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if r.status_code == 200 and r.json().get("ok"):
            logger.info("Telegram: sendPhoto OK")
            return True
        logger.warning("Telegram: sendPhoto rejected — %s", r.text[:200])
        return False
    except Exception as exc:
        logger.warning("Telegram: sendPhoto exception — %s", exc)
        return False


def _send_message(token: str, channel_id: str, text: str) -> bool:
    url = _TELEGRAM_API.format(token=token, method="sendMessage")
    try:
        r = requests.post(
            url,
            json={
                "chat_id": channel_id,
                "text": text[:_MAX_MESSAGE],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if r.status_code == 200 and r.json().get("ok"):
            logger.info("Telegram: sendMessage OK")
            return True
        logger.warning("Telegram: sendMessage rejected — %s", r.text[:200])
        return False
    except Exception as exc:
        logger.warning("Telegram: sendMessage exception — %s", exc)
        return False
