#!/usr/bin/env python3
"""
CLI entrypoint for ingesting one art image.

Usage:
    python scripts/ingest.py --source-url URL --source-id ID [--download-image]

Steps:
    1. Validate source against approved_sources registry
    2. Create source record
    3. Run Pass 1 (literal description)
    4. Safety gate — hard reject if blocked categories detected
    5. Run Pass 2 (constrained interpretation) if Pass 1 is clean
    6. Run recurrence check
    7. Write Obsidian note + save interpretation record
    8. Run rarity scorer + save rarity record
    9. Send to Telegram if enabled and above threshold

Exit codes:
    0 — success
    1 — source not approved
    2 — flagged for human review
    3 — safety rejected or safety uncertain (hard stop)
"""

import sys
import os
import json
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import click
from src.ingest.source_registry import load_approved_sources, validate_source_url
from src.ingest.source_record import create_source_record, save_source_record
from src.interpret.pipeline import run_two_pass_pipeline
from src.recurrence.checker import run_recurrence_check
from src.obsidian_writer.writer import write_image_note
from src.scoring.rarity_scorer import run_rarity_scorer
from src.telegram import send_if_eligible, is_enabled as telegram_enabled

_REJECTED_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "flags", "rejected"
)
_RECORDS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "records"
)
_SAFETY_STOP_STATUSES = {"safety_rejected", "safety_uncertain"}


@click.command()
@click.option("--source-url", required=True, help="URL of the art page or image to ingest")
@click.option("--source-id", required=True, help="Stable ID for this source, e.g. src_20260417_001")
@click.option("--title", default=None, help="Title of the artwork if known")
@click.option("--artist", default=None, help="Artist name if known")
@click.option("--image-url", default=None, help="Direct image URL if different from source-url")
@click.option("--download-image", is_flag=True, default=False, help="Download image locally before processing")
@click.option("--dry-run", is_flag=True, default=False, help="Run pipeline but do not write any files")
def ingest(source_url, source_id, title, artist, image_url, download_image, dry_run):
    """Ingest one art image through the two-pass interpretation pipeline."""

    click.echo(f"[ingest] Starting ingest for {source_id}: {source_url}")

    # Step 1: Validate source
    approved_sources = load_approved_sources()
    if not validate_source_url(source_url, approved_sources):
        click.echo(
            f"[BLOCKED] {source_url} is not from an approved source. "
            "Add it to data/sources/approved_sources.json first.",
            err=True,
        )
        sys.exit(1)

    # Step 2: Create source record
    source_record = create_source_record(
        source_id=source_id,
        url=source_url,
        image_url=image_url or source_url,
        title=title,
        artist=artist,
        download_image=download_image,
    )

    if not dry_run:
        save_source_record(source_record)
        click.echo(f"[ingest] Source record saved: data/sources/{source_id}.json")

    # Steps 3–5: Two-pass pipeline (includes safety gate internally)
    click.echo("[ingest] Running two-pass interpretation pipeline...")
    interpretation_record = run_two_pass_pipeline(source_record)

    # Step 4 (enforced): Safety gate — hard stop before any output
    gov_status = interpretation_record["governance"]["review_status"]
    if gov_status in _SAFETY_STOP_STATUSES:
        _handle_safety_rejection(
            interpretation_record=interpretation_record,
            source_record=source_record,
            dry_run=dry_run,
        )
        sys.exit(3)

    # Pass 1 governance check
    if not interpretation_record["pass1"].get("pass1_clean", False):
        click.echo("[FLAG] Pass 1 is not clean. Record flagged for human review.", err=True)
        interpretation_record["governance"]["review_status"] = "flagged"
        interpretation_record["governance"]["flag_reason"] = "Pass 1 contains inference"

    # Pass 2 governance check
    if not interpretation_record["pass2"].get("prohibited_inference_check", {}).get("passed", False):
        click.echo("[FLAG] Prohibited inference detected. Record flagged for human review.", err=True)
        interpretation_record["governance"]["review_status"] = "flagged"
        violations = interpretation_record["pass2"].get("prohibited_inference_check", {}).get("violations", [])
        interpretation_record["governance"]["flag_reason"] = f"{len(violations)} prohibited inference(s) found"

    # Step 6: Recurrence check
    click.echo("[ingest] Running recurrence check...")
    recurrence_matches = run_recurrence_check(interpretation_record)
    interpretation_record["pass2"]["recurrence_references"] = recurrence_matches
    if recurrence_matches:
        click.echo(f"[ingest] Recurrence: {len(recurrence_matches)} match(es) found")

    # Step 7: Save interpretation record + write Obsidian note
    if not dry_run:
        from src.ingest.record_store import save_interpretation_record
        save_interpretation_record(interpretation_record)
        note_path = write_image_note(interpretation_record, source_record)
        click.echo(f"[ingest] Obsidian note written: {note_path}")
    else:
        click.echo("[dry-run] No files written.")

    # Step 8: Rarity scoring
    click.echo("[ingest] Running rarity scorer...")
    rarity_record = run_rarity_scorer(interpretation_record)
    if "error" not in rarity_record:
        click.echo(
            f"[ingest] Rarity score: {rarity_record.get('rarity_score', 0):.2f} "
            f"(keep={rarity_record.get('keep')})"
        )
        if not dry_run:
            _save_rarity_record(rarity_record)
    else:
        click.echo(f"[ingest] Rarity scorer error: {rarity_record.get('error')}", err=True)

    # Step 9: Telegram (optional — does nothing if disabled)
    if not dry_run and telegram_enabled():
        sent = send_if_eligible(interpretation_record, rarity_record, source_record)
        if sent:
            click.echo("[ingest] Sent to Telegram.")
        else:
            click.echo("[ingest] Telegram: not sent (below threshold or ineligible).")

    # Final status
    status = interpretation_record["governance"]["review_status"]
    click.echo(f"[ingest] Complete. Review status: {status}")
    if status == "flagged":
        click.echo(f"[ingest] Flag reason: {interpretation_record['governance'].get('flag_reason')}")
        sys.exit(2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_rarity_record(rarity_record: dict) -> None:
    os.makedirs(_RECORDS_DIR, exist_ok=True)
    path = os.path.join(_RECORDS_DIR, f"{rarity_record['rarity_record_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rarity_record, f, indent=2)


def _handle_safety_rejection(
    interpretation_record: dict,
    source_record: dict,
    dry_run: bool,
) -> None:
    """Write a rejection record and print a clear rejection message.

    No interpretation record, Obsidian note, Telegram message, or social
    output is written. This function only writes the rejection record.
    """
    gov = interpretation_record["governance"]
    safety = interpretation_record.get("safety", {})
    record_id = interpretation_record["record_id"]
    source_id = interpretation_record["source_id"]
    status = gov["review_status"]

    rejection_id = record_id.replace("rec_", "rej_")

    raw_description = interpretation_record.get("pass1", {}).get("description", "")
    excerpt = raw_description[:300] + ("..." if len(raw_description) > 300 else "")

    rejection_record = {
        "rejection_id": rejection_id,
        "source_id": source_id,
        "record_id": record_id,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "gate_version": "1.0",
        "disposition": status,
        "tier": safety.get("tier", "unknown"),
        "matched_categories": safety.get("matched_categories", []),
        "rejection_reason": gov.get("flag_reason", ""),
        "pass1_description_excerpt": excerpt,
        "outputs_suppressed": [
            "interpretation_record",
            "obsidian_note",
            "telegram",
            "social",
        ],
    }

    if not dry_run:
        os.makedirs(_REJECTED_DIR, exist_ok=True)
        rejection_path = os.path.join(_REJECTED_DIR, f"{rejection_id}.json")
        with open(rejection_path, "w", encoding="utf-8") as f:
            json.dump(rejection_record, f, indent=2)
        click.echo(f"[REJECTED] Rejection record written: {rejection_path}", err=True)
    else:
        click.echo("[dry-run] Rejection record not written (dry-run mode).", err=True)

    label = "SAFETY UNCERTAIN" if status == "safety_uncertain" else "SAFETY REJECTED"
    click.echo(f"[{label}] {gov.get('flag_reason', '')}", err=True)
    click.echo(
        f"[{label}] Suppressed: interpretation record, Obsidian note, Telegram, social output.",
        err=True,
    )


if __name__ == "__main__":
    ingest()
