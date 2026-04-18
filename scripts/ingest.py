#!/usr/bin/env python3
"""
CLI entrypoint for ingesting one art image.

Usage:
    python scripts/ingest.py --source-url URL --source-id ID [--download-image]

Steps:
    1. Validate source against approved_sources registry
    2. Create source record
    3. Run Pass 1 (literal description)
    4. Run Pass 2 (constrained interpretation) if Pass 1 is clean
    5. Run recurrence check
    6. Write Obsidian note
    7. Flag for human review if any governance check fails
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import click
from src.ingest.source_registry import load_approved_sources, validate_source_url
from src.ingest.source_record import create_source_record, save_source_record
from src.interpret.pipeline import run_two_pass_pipeline
from src.recurrence.checker import run_recurrence_check
from src.obsidian_writer.writer import write_image_note


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
        click.echo(f"[BLOCKED] {source_url} is not from an approved source. Add it to data/sources/approved_sources.json first.", err=True)
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

    # Step 3 + 4: Two-pass interpretation pipeline
    click.echo("[ingest] Running two-pass interpretation pipeline...")
    interpretation_record = run_two_pass_pipeline(source_record)

    if not interpretation_record["pass1"]["pass1_clean"]:
        click.echo("[FLAG] Pass 1 is not clean. Record flagged for human review.", err=True)
        interpretation_record["governance"]["review_status"] = "flagged"
        interpretation_record["governance"]["flag_reason"] = "Pass 1 contains inference"

    if not interpretation_record["pass2"]["prohibited_inference_check"]["passed"]:
        click.echo("[FLAG] Prohibited inference detected. Record flagged for human review.", err=True)
        interpretation_record["governance"]["review_status"] = "flagged"
        violations = interpretation_record["pass2"]["prohibited_inference_check"]["violations"]
        interpretation_record["governance"]["flag_reason"] = f"{len(violations)} prohibited inference(s) found"

    # Step 5: Recurrence check
    click.echo("[ingest] Running recurrence check...")
    recurrence_matches = run_recurrence_check(interpretation_record)
    interpretation_record["pass2"]["recurrence_references"] = recurrence_matches
    if recurrence_matches:
        click.echo(f"[ingest] Recurrence: {len(recurrence_matches)} match(es) found")

    # Step 6: Write Obsidian note
    if not dry_run:
        from src.ingest.record_store import save_interpretation_record
        save_interpretation_record(interpretation_record)
        note_path = write_image_note(interpretation_record, source_record)
        click.echo(f"[ingest] Obsidian note written: {note_path}")
    else:
        click.echo("[dry-run] No files written.")

    # Final status
    status = interpretation_record["governance"]["review_status"]
    click.echo(f"[ingest] Complete. Review status: {status}")
    if status == "flagged":
        click.echo(f"[ingest] Flag reason: {interpretation_record['governance'].get('flag_reason')}")
        sys.exit(2)


if __name__ == "__main__":
    ingest()
