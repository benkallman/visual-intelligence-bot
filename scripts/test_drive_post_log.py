#!/usr/bin/env python3
"""Exercise the Google Drive post logger without posting to X."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from src.drive.post_log import log_post

LOG_PATH = ROOT_DIR / "data" / "social_post_log.json"


def _load_log() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _latest_posted_entry() -> dict:
    for entry in reversed(_load_log()):
        if entry.get("status") == "posted" and entry.get("folder"):
            return entry
    raise RuntimeError("no posted entries found in data/social_post_log.json")


def _resolve_sample(entry: dict) -> tuple[dict, Path, Path, str]:
    folder = Path(entry["folder"]).resolve()
    post_path = folder / "post.txt"
    image_path = Path(entry.get("local_image_path") or (folder / "image.jpg")).resolve()
    if not post_path.is_file():
        raise RuntimeError(f"missing post.txt in {folder}")
    if not image_path.is_file():
        raise RuntimeError(f"missing image file: {image_path}")

    sample_entry = dict(entry)
    sample_entry.setdefault(
        "posted_at",
        datetime.datetime.now().isoformat(timespec="seconds"),
    )
    return sample_entry, folder, image_path, post_path.read_text(encoding="utf-8").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Google Drive post logging.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Build the row and print it without Google API calls")
    mode.add_argument("--send-test", action="store_true", help="Upload the sample image and append a live doc row")
    args = parser.parse_args()

    entry, folder, image_path, post_text = _resolve_sample(_latest_posted_entry())
    result = log_post(
        entry=entry,
        folder=folder,
        image_path=image_path,
        post_text=post_text,
        root_dir=ROOT_DIR,
        dry_run=not args.send_test,
    )

    sys.stdout.buffer.write(json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
    if args.send_test:
        return 0 if result.get("status") == "logged" else 1
    return 0 if result.get("status") == "dry_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
