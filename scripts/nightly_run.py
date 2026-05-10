#!/usr/bin/env python3
"""
Nightly discovery and ingest run.

Usage:
    python scripts/nightly_run.py [--dry-run] [--max-total N]

Reads data/sources/nightly_sources.json, validates each category_url against
approved_sources.json, discovers candidates via the Wikimedia Commons category
API, runs the existing ingest pipeline on each, and writes a nightly summary
to reports/nightly/YYYY-MM-DD-summary.{json,md}.

Does not schedule itself. Does not post to social media.
"""

import argparse
import datetime
import io
import json
import os
import random
import subprocess
import sys

# Reconfigure stdout/stderr to UTF-8 on Windows (cp1252 default crashes on
# non-Latin art titles). errors='replace' keeps the run alive if a character
# still can't be encoded after reconfiguration.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.discovery.wikimedia import discover_candidate, discover_from_category
from src.ingest.source_registry import load_approved_sources, validate_source_url
from src.scoring.viral_scorer import run_viral_scorer

NIGHTLY_SOURCES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "sources", "nightly_sources.json"
)
RECORDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "records")
SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sources")
REJECTED_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "flags", "rejected"
)
RARITY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "rarity")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "nightly")
INGEST_SCRIPT = os.path.join(os.path.dirname(__file__), "ingest.py")

_EXIT_LABEL = {0: "accepted", 2: "flagged", 3: "rejected"}

DEFAULT_MAX_TOTAL = 20
DEFAULT_MAX_SOURCES = int(os.environ.get("NIGHTLY_MAX_SOURCES", "2"))


def _load_nightly_sources() -> list[dict]:
    if not os.path.isfile(NIGHTLY_SOURCES_PATH):
        print(f"[nightly] ERROR: nightly_sources.json not found at {NIGHTLY_SOURCES_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(NIGHTLY_SOURCES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("sources", [])


def _source_id_from_candidate(candidate: dict) -> str:
    return candidate["candidate_id"].replace("cand_", "src_", 1)


def _already_processed(source_id: str) -> bool:
    record_id = source_id.replace("src_", "rec_", 1)
    rejection_id = source_id.replace("src_", "rej_", 1)
    return any(
        os.path.isfile(path)
        for path in (
            os.path.join(SOURCES_DIR, f"{source_id}.json"),
            os.path.join(RECORDS_DIR, f"{record_id}.json"),
            os.path.join(REJECTED_DIR, f"{rejection_id}.json"),
        )
    )


def _run_ingest(candidate: dict, source_id: str, dry_run: bool) -> int:
    source_url = candidate.get("direct_image_url") or candidate["page_url"]
    cmd = [
        sys.executable, INGEST_SCRIPT,
        "--source-url", source_url,
        "--source-id", source_id,
    ]
    if candidate.get("title"):
        cmd += ["--title", candidate["title"]]
    if candidate.get("artist"):
        cmd += ["--artist", candidate["artist"]]
    if dry_run:
        cmd += ["--dry-run"]
    return subprocess.run(cmd, capture_output=False).returncode


def _resolution_score(candidate: dict) -> int:
    width = candidate.get("width")
    height = candidate.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        return 0
    return width * height


def _has_title_and_artist(candidate: dict) -> bool:
    return bool(candidate.get("title") and candidate.get("artist"))


def _select_fallback_candidate(candidates: list[dict]) -> dict | None:
    eligible = [
        candidate for candidate in candidates
        if not candidate.get("already_processed")
        and candidate.get("status") not in {"accepted", "flagged", "rejected", "errored"}
    ]
    if not eligible:
        return None

    if not any(_resolution_score(item["candidate"]) or _has_title_and_artist(item["candidate"]) for item in eligible):
        return eligible[0]

    return max(
        eligible,
        key=lambda item: (
            _resolution_score(item["candidate"]),
            int(_has_title_and_artist(item["candidate"])),
            -item["discovery_index"],
        ),
    )


def _write_summary(summary: dict, date_str: str, dry_run: bool) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    base = os.path.join(REPORTS_DIR, f"{date_str}-summary")

    if not dry_run:
        with open(f"{base}.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"[nightly] Summary JSON: reports/nightly/{date_str}-summary.json")

    md = _render_md(summary)
    if not dry_run:
        with open(f"{base}.md", "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[nightly] Summary MD:   reports/nightly/{date_str}-summary.md")

    print()
    print(md)


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _record_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'rec_', 1)}.json")


def _source_path(source_id: str) -> str:
    return os.path.join(SOURCES_DIR, f"{source_id}.json")


def _rarity_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'rar_', 1)}.json")


def _rarity_detection_path(source_id: str) -> str:
    return os.path.join(RARITY_DIR, f"{source_id.replace('src_', 'rdt_', 1)}.json")


def _viral_path(source_id: str) -> str:
    return os.path.join(RECORDS_DIR, f"{source_id.replace('src_', 'vir_', 1)}.json")


def _save_viral_record(record: dict) -> None:
    os.makedirs(RECORDS_DIR, exist_ok=True)
    with open(_viral_path(record["source_id"]), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)


def _render_md(summary: dict) -> str:
    date_str = summary["date"]
    lines = [
        f"# Nightly Run - {date_str}",
        "",
        f"Run at: {summary['run_at']}  dry_run: {summary['dry_run']}",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Sources configured | {summary['sources_configured']} |",
        f"| Sources valid | {summary['sources_valid']} |",
        f"| Candidates discovered | {summary['candidates_discovered']} |",
        f"| Skipped (already processed) | {summary['skipped']} |",
        f"| Accepted | {summary['accepted']} |",
        f"| Flagged | {summary['flagged']} |",
        f"| Rejected | {summary['rejected']} |",
        f"| Errored | {summary['errored']} |",
        "",
    ]

    if summary.get("items"):
        lines += ["## Items", ""]
        for item in summary["items"]:
            status = "ACCEPTED — FALLBACK" if item.get("accepted_fallback") else item["status"].upper()
            title = item.get("title") or item["source_id"]
            lines.append(f"- [{status}] **{title}** (`{item['source_id']}`)")
            if item.get("url"):
                lines.append(f"  {item['url']}")
            if item.get("rarity_score") is not None:
                lines.append(f"  rarity_score: {item['rarity_score']:.2f}")
            if item.get("viral_score") is not None:
                lines.append(f"  viral_score: {item['viral_score']:.2f}  use: {item.get('recommended_use', 'archive')}")
        lines.append("")

    if summary.get("invalid_sources"):
        lines += ["## Skipped Sources (not approved)", ""]
        for entry in summary["invalid_sources"]:
            lines.append(f"- {entry['label']}: `{entry['category_url']}`")
        lines.append("")

    return "\n".join(lines)


def _attach_rarity_score(item: dict) -> None:
    source_id = item.get("source_id")
    if not source_id or source_id == "unknown":
        return

    rarity_record = _load_json(_rarity_detection_path(source_id))
    if not rarity_record or "error" in rarity_record:
        return

    item["rarity_score"] = rarity_record.get("rarity_score")


def _attach_viral_score(item: dict, dry_run: bool) -> None:
    if item.get("status") != "accepted":
        return

    source_id = item["source_id"]
    record = _load_json(_record_path(source_id))
    source = _load_json(_source_path(source_id))
    if not record or not source:
        return

    viral_record = _load_json(_viral_path(source_id))
    if viral_record is None:
        rarity_record = _load_json(_rarity_path(source_id))
        viral_record = run_viral_scorer(record, source, rarity_record=rarity_record)
        if "error" not in viral_record and not dry_run:
            _save_viral_record(viral_record)

    if "error" in viral_record:
        return

    item["viral_score"] = viral_record.get("viral_score")
    item["recommended_use"] = viral_record.get("recommended_use")


def _sort_summary_items(items: list[dict]) -> list[dict]:
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            pair[1].get("status") != "accepted",
            -(pair[1].get("viral_score", -1.0) if pair[1].get("viral_score") is not None else -1.0),
            pair[0],
        )
    )
    return [item for _, item in indexed]


def main(dry_run: bool, max_total: int, max_sources: int) -> None:
    run_at = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    date_str = datetime.date.today().isoformat()

    print(f"[nightly] Starting nightly run. date={date_str} dry_run={dry_run} max_total={max_total}")

    nightly_sources = _load_nightly_sources()
    total_sources = len(nightly_sources)
    random.shuffle(nightly_sources)
    nightly_sources = nightly_sources[:max_sources]
    print(f"[nightly] rotating sources: selected {len(nightly_sources)} of {total_sources}")

    approved = load_approved_sources()

    summary: dict = {
        "date": date_str,
        "run_at": run_at,
        "dry_run": dry_run,
        "sources_configured": total_sources,
        "sources_valid": 0,
        "candidates_discovered": 0,
        "skipped": 0,
        "accepted": 0,
        "flagged": 0,
        "rejected": 0,
        "errored": 0,
        "items": [],
        "invalid_sources": [],
    }

    fallback_candidates: list[dict] = []
    total_processed = 0
    new_candidates_count = 0

    for source_entry in nightly_sources:
        label = source_entry.get("label", "unlabeled")
        category_url = source_entry.get("category_url", "")
        source_limit = source_entry.get("limit", 5)

        if not validate_source_url(category_url, approved):
            print(f"[nightly] SKIP (not approved): {label} - {category_url}", file=sys.stderr)
            summary["invalid_sources"].append({"label": label, "category_url": category_url})
            continue

        summary["sources_valid"] += 1
        print(f"\n[nightly] Source: {label} (limit={source_limit})")
        print(f"          {category_url}")

        try:
            file_urls = discover_from_category(category_url, limit=source_limit)
        except Exception as exc:
            print(f"  [ERROR] Category discovery failed: {exc}", file=sys.stderr)
            continue

        print(f"  discovered {len(file_urls)} file URL(s)")

        for file_url in file_urls:
            try:
                candidate = discover_candidate(file_url)
            except Exception as exc:
                print(f"  [ERROR] discover_candidate failed for {file_url}: {exc}", file=sys.stderr)
                summary["errored"] += 1
                summary["items"].append({
                    "source_id": "unknown",
                    "title": None,
                    "url": file_url,
                    "status": "errored",
                    "error": str(exc),
                })
                continue

            source_id = _source_id_from_candidate(candidate)
            title = candidate.get("title")
            already_processed = _already_processed(source_id)
            summary["candidates_discovered"] += 1

            fallback_entry = {
                "candidate": candidate,
                "source_id": source_id,
                "title": title,
                "url": file_url,
                "already_processed": already_processed,
                "discovery_index": len(fallback_candidates),
                "status": None,
            }
            fallback_candidates.append(fallback_entry)

            if already_processed:
                print(f"[nightly] skipping known source: {source_id}")
                summary["skipped"] += 1
                summary["items"].append({
                    "source_id": source_id,
                    "title": title,
                    "url": file_url,
                    "status": "skipped",
                })
                fallback_entry["status"] = "skipped"
                continue

            new_candidates_count += 1

            if total_processed >= max_total:
                continue

            print(f"  INGEST {source_id} - {title or file_url}")
            code = _run_ingest(candidate, source_id, dry_run)
            status = _EXIT_LABEL.get(code, "errored")
            fallback_entry["status"] = status

            summary[status if status in ("accepted", "flagged", "rejected", "errored") else "errored"] += 1
            summary["items"].append({
                "source_id": source_id,
                "title": title,
                "url": file_url,
                "status": status,
                "exit_code": code,
            })
            total_processed += 1

    if new_candidates_count == 0:
        print("[nightly] no new candidates found — consider expanding sources")

    if summary["accepted"] == 0 and summary["errored"] == 0:
        fallback = _select_fallback_candidate(fallback_candidates)
        if fallback is not None:
            print(f"[nightly] FALLBACK promote {fallback['source_id']} - {fallback['title'] or fallback['url']}")
            summary["accepted"] += 1
            summary["items"].append({
                "source_id": fallback["source_id"],
                "title": fallback["title"],
                "url": fallback["url"],
                "status": "accepted",
                "accepted_fallback": True,
            })

    for item in summary["items"]:
        _attach_rarity_score(item)
        _attach_viral_score(item, dry_run=dry_run)

    summary["items"] = _sort_summary_items(summary["items"])

    print()
    print("=" * 55)
    print(
        f"[nightly] SUMMARY  accepted={summary['accepted']}  "
        f"flagged={summary['flagged']}  rejected={summary['rejected']}  "
        f"errored={summary['errored']}  skipped={summary['skipped']}"
    )
    print("=" * 55)

    _write_summary(summary, date_str, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nightly discovery and ingest run.")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without writing files or reports")
    parser.add_argument(
        "--max-total",
        type=int,
        default=DEFAULT_MAX_TOTAL,
        help=f"Max candidates to process across all sources (default {DEFAULT_MAX_TOTAL})",
    )
    parser.add_argument(
        "--max-sources",
        type=int,
        default=DEFAULT_MAX_SOURCES,
        help=f"Max sources to pull from per run, env NIGHTLY_MAX_SOURCES (default {DEFAULT_MAX_SOURCES})",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, max_total=args.max_total, max_sources=args.max_sources)
