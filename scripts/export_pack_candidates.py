#!/usr/bin/env python3
"""
Export a human-review list of candidates from a completed source pack run.

Reads:
  data/candidates/cand_*.json   — filtered by pack_id field
  data/sources/src_*.json       — for title, url, rights (if ingested)
  data/records/rar_*.json       — rarity scorer output
  data/rarity/rdt_*.json        — rarity detector output

Writes:
  exports/source-packs/<pack_id>/<date>/candidates.json
  exports/source-packs/<pack_id>/<date>/candidates.md

Items are sorted by rarity_score descending. Items not yet ingested are
listed at the bottom with status "candidate only".

Does NOT post to X. Does NOT touch post_daily_queue.py.

Usage:
    python scripts/export_pack_candidates.py --pack japanese_wood_historical
    python scripts/export_pack_candidates.py --pack japanese_wood_historical --date 2026-05-11
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = ROOT_DIR / "data" / "candidates"
SOURCES_DIR = ROOT_DIR / "data" / "sources"
RECORDS_DIR = ROOT_DIR / "data" / "records"
RARITY_DIR = ROOT_DIR / "data" / "rarity"
EXPORTS_DIR = ROOT_DIR / "exports" / "source-packs"

_COMBINED_WEIGHTS = (0.45, 0.35, 0.20)  # rarity, viral, brand_fit
_BRAND_FIT_DEFAULT = 0.5                 # assumed when viral_score absent


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_pack_candidates(pack_id: str) -> list[dict]:
    """Return all candidate records whose pack_id matches."""
    if not CANDIDATES_DIR.exists():
        return []
    results = []
    for path in sorted(CANDIDATES_DIR.glob("cand_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("pack_id") == pack_id:
            results.append(data)
    return results


def _find_rarity_score(source_id: str) -> float | None:
    """Try rarity scorer output first, then rarity detector."""
    rar_id = source_id.replace("src_", "rar_", 1)
    rec = _load_json(RECORDS_DIR / f"{rar_id}.json")
    if rec and rec.get("rarity_score") is not None:
        return float(rec["rarity_score"])
    rdt_id = source_id.replace("src_", "rdt_", 1)
    rec = _load_json(RARITY_DIR / f"{rdt_id}.json")
    if rec and rec.get("rarity_score") is not None:
        return float(rec["rarity_score"])
    return None


def _find_keep(source_id: str) -> bool | None:
    rar_id = source_id.replace("src_", "rar_", 1)
    rec = _load_json(RECORDS_DIR / f"{rar_id}.json")
    if rec:
        return rec.get("keep")
    return None


def _find_viral_score(source_id: str) -> float | None:
    vir_id = source_id.replace("src_", "vir_", 1)
    rec = _load_json(RECORDS_DIR / f"{vir_id}.json")
    if rec and rec.get("viral_score") is not None:
        return float(rec["viral_score"])
    return None


def _combined_score(rarity: float | None, viral: float | None) -> float | None:
    if rarity is None:
        return None
    r_w, v_w, b_w = _COMBINED_WEIGHTS
    v = viral if viral is not None else _BRAND_FIT_DEFAULT
    return r_w * rarity + v_w * v + b_w * _BRAND_FIT_DEFAULT


def _recommended_use(rarity: float | None, viral: float | None) -> str:
    score = _combined_score(rarity, viral)
    if score is None:
        return "not yet scored"
    if score >= 0.65:
        return "high priority — queue for social"
    if score >= 0.45:
        return "medium — review for social or archive"
    return "low — archive only"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(pack_id: str, date_str: str) -> None:
    sys.stdout.reconfigure(errors="replace")
    candidates = _load_pack_candidates(pack_id)

    print(f"[export-pack] pack={pack_id}")
    print(f"[export-pack] date={date_str}")
    print(f"[export-pack] candidates with pack_id={pack_id!r}: {len(candidates)}")

    items = []
    for cand in candidates:
        cid = cand["candidate_id"]
        sid = cid.replace("cand_", "src_", 1)

        source = _load_json(SOURCES_DIR / f"{sid}.json")
        rarity_score = _find_rarity_score(sid)
        viral_score = _find_viral_score(sid)
        keep = _find_keep(sid)

        status = "ingested" if source else "candidate only"

        items.append({
            "candidate_id": cid,
            "source_id": sid,
            "title": cand.get("title") or (source or {}).get("title") or "Unknown",
            "artist": cand.get("artist") or (source or {}).get("artist"),
            "date": cand.get("date_raw") or (source or {}).get("date_created"),
            "date_year": cand.get("date_year"),
            "source_url": cand.get("page_url"),
            "license": cand.get("license"),
            "pack_query": cand.get("pack_query"),
            "status": status,
            "keep": keep,
            "rarity_score": rarity_score,
            "viral_score": viral_score,
            "combined_score": _combined_score(rarity_score, viral_score),
            "recommended_use": _recommended_use(rarity_score, viral_score),
        })

    # Sort: scored items first (by combined_score desc), then unscored
    items.sort(
        key=lambda x: (x["combined_score"] is not None, x["combined_score"] or 0),
        reverse=True,
    )

    out_dir = EXPORTS_DIR / pack_id / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- JSON output ---
    json_path = out_dir / "candidates.json"
    json_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Markdown output ---
    md_lines = [
        f"# Source Pack: {pack_id}",
        f"## Candidates — {date_str}",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Pack | {pack_id} |",
        f"| Export date | {date_str} |",
        f"| Total candidates | {len(items)} |",
        f"| Ingested | {sum(1 for x in items if x['status'] == 'ingested')} |",
        f"| Scored | {sum(1 for x in items if x['rarity_score'] is not None)} |",
        "",
        "---",
        "",
    ]

    for i, item in enumerate(items, 1):
        r = f"{item['rarity_score']:.2f}" if item["rarity_score"] is not None else "—"
        v = f"{item['viral_score']:.2f}" if item["viral_score"] is not None else "—"
        c = f"{item['combined_score']:.2f}" if item["combined_score"] is not None else "—"
        md_lines += [
            f"### {i}. {item['title']}",
            "",
            f"- **Status:** {item['status']}",
            f"- **Date:** {item['date'] or '—'}  (year={item['date_year'] or '—'})",
            f"- **Artist:** {item['artist'] or '—'}",
            f"- **License:** {item['license'] or '—'}",
            f"- **Source:** {item['source_url']}",
            f"- **Rarity score:** {r}",
            f"- **Viral score:** {v}",
            f"- **Combined score:** {c}",
            f"- **Keep:** {item['keep']}",
            f"- **Recommended use:** {item['recommended_use']}",
            f"- **Pack query:** {item['pack_query'] or '—'}",
            "",
        ]

    md_path = out_dir / "candidates.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"[export-pack] Written: {json_path}")
    print(f"[export-pack] Written: {md_path}")
    print()

    # Print top-5 summary to stdout
    scored = [x for x in items if x["rarity_score"] is not None]
    if scored:
        print(f"[export-pack] Top {min(5, len(scored))} by rarity score:")
        for item in scored[:5]:
            r = f"{item['rarity_score']:.2f}"
            print(f"  [{r}] {item['title'][:60]}  (year={item['date_year'] or '?'})")
    else:
        print("[export-pack] No scored items yet - run run_source_pack.py first.")
        print(f"[export-pack] Unscored candidates ({len(items)}):")
        for item in items[:10]:
            print(f"  {item['title'][:60]}  (year={item['date_year'] or '?'}) [{item['status']}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a candidate review list for a source pack.")
    parser.add_argument("--pack", required=True, help="Pack ID (matches data/source_packs/<pack>.json)")
    parser.add_argument("--date", default="today", help="Date label for the export directory (default: today)")
    args = parser.parse_args()
    try:
        main(pack_id=args.pack, date_str=_resolve_date(args.date))
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[export-pack] Error: {exc}", file=sys.stderr)
        sys.exit(1)
