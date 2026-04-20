#!/usr/bin/env python3
"""
Discover Wikimedia Commons candidates and save them to data/candidates/.

Usage:
    python scripts/discover_wikimedia_candidates.py URL [URL ...]
    python scripts/discover_wikimedia_candidates.py --dry-run URL [URL ...]

Each URL must be a Commons file-page URL, e.g.:
    https://commons.wikimedia.org/wiki/File:Example.jpg
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import click
from src.discovery.wikimedia import discover_candidate, save_candidate


@click.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, default=False, help="Fetch metadata but do not write files")
def discover(urls, dry_run):
    """Fetch Commons metadata for each URL and save candidate JSON records."""
    success = 0
    for url in urls:
        click.echo(f"[discover] {url}")
        try:
            record = discover_candidate(url)
        except Exception as exc:
            click.echo(f"  [ERROR] {exc}", err=True)
            continue

        click.echo(f"  candidate_id:      {record['candidate_id']}")
        click.echo(f"  title:             {record['title']}")
        click.echo(f"  direct_image_url:  {record['direct_image_url']}")

        if dry_run:
            click.echo("  [dry-run] not saved")
        else:
            path = save_candidate(record)
            click.echo(f"  saved: {path}")

        success += 1

    click.echo(f"\n[discover] Done. {success}/{len(urls)} candidates processed.")


if __name__ == "__main__":
    discover()
