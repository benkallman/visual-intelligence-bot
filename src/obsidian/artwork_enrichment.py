"""
Artwork enrichment for Obsidian notes linked to social queue folders.

Reads all available local metadata for a queue folder — metadata.json,
candidate record, source record — and creates or updates a structured
Obsidian note with source-grounded content.  No hallucination: all
claims are metadata-derived and labeled provisional where uncertain.

Public API:
  enrich_folder(folder, date_str, rank, root_dir, dry_run) → dict
  append_posting_log_entry(folder, entry, post_text, drive_url, root_dir) → bool
  find_note_path(meta, folder, date_str, rank, root_dir) → Path
  is_generic_caption(text) → bool
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]

_GENERIC_CAPTION_RE = re.compile(
    r"(historical image\s*--\s*medium and period from source metadata"
    r"|medium and period connect to"
    r"|historical work,?\s+\d{4}\.\s+historical image)",
    re.IGNORECASE,
)

# Pack-id → context hints for known packs.
_PACK_CONTEXT: dict[str, dict[str, str]] = {
    "japanese_wood_historical": {
        "culture": "Japanese",
        "medium": "Woodblock print",
        "region": "Japan",
        "period": "Edo/Meiji period",
    },
    "tibetan_thangka": {
        "culture": "Tibetan",
        "medium": "Thangka painting",
        "region": "Tibet / Himalayan",
        "period": "Tibetan Buddhist tradition",
    },
    "dutch_golden_age": {
        "culture": "Dutch",
        "region": "Netherlands",
        "period": "Dutch Golden Age",
    },
    "chinese_imperial": {
        "culture": "Chinese",
        "region": "China",
        "period": "Imperial Chinese",
    },
    "european_medieval": {
        "culture": "European",
        "period": "Medieval period",
    },
    "islamic_manuscript": {
        "culture": "Islamic",
        "medium": "Manuscript illustration",
        "period": "Islamic manuscript tradition",
    },
}

_COLLECTION_MAP = [
    ("rijksmuseum", "Rijksmuseum, Amsterdam"),
    ("metmuseum", "The Metropolitan Museum of Art"),
    ("britishmuseum", "The British Museum"),
    ("louvre", "The Louvre"),
    ("artic.edu", "Art Institute of Chicago"),
    ("vam.ac.uk", "Victoria and Albert Museum"),
    ("smithsonian", "Smithsonian Institution"),
    ("nga.gov", "National Gallery of Art"),
    ("harvardartmuseums", "Harvard Art Museums"),
    ("clevelandart", "Cleveland Museum of Art"),
    ("lacma", "Los Angeles County Museum of Art"),
    ("mfa.org", "Museum of Fine Arts, Boston"),
    ("commons.wikimedia", "Wikimedia Commons"),
    ("wikimedia", "Wikimedia Commons"),
]

_POSTING_LOG_MARKER = "## Posting Log"
_HISTORICAL_CTX_MARKER = "## Historical Context"
_SOCIAL_CAPTION_MARKER = "## Social Caption Drafts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_generic_caption(text: str) -> bool:
    return bool(_GENERIC_CAPTION_RE.search(text))


def _read_json(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_text(path: pathlib.Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _infer_collection(url: str) -> str:
    url_lower = url.lower()
    for fragment, name in _COLLECTION_MAP:
        if fragment in url_lower:
            return name
    return ""


def _str_val(meta: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        v = meta.get(key)
        if v is not None:
            s = str(v).strip()
            if s:
                return s
    return default


def _load_all_metadata(folder: pathlib.Path, root_dir: pathlib.Path) -> dict:
    """Merge metadata.json with candidate and source records for a queue folder."""
    meta = _read_json(folder / "metadata.json")

    candidate_id = _str_val(meta, "candidate_id")
    if candidate_id:
        cand = _read_json(root_dir / "data" / "candidates" / f"{candidate_id}.json")
        for field in ("date_raw", "date_year", "license", "pack_query", "page_url"):
            if cand.get(field) and not meta.get(field):
                meta[field] = cand[field]

    source_id = _str_val(meta, "source_id")
    if source_id:
        src = _read_json(root_dir / "data" / "sources" / f"{source_id}.json")
        for field in ("medium", "date_created", "dimensions", "collection", "rights_flag"):
            if src.get(field) and not meta.get(field):
                meta[field] = src[field]

    # Normalize date_year to a string
    if not meta.get("date_year"):
        y = meta.get("year")
        if y is not None:
            meta["date_year"] = str(int(y)) if isinstance(y, (int, float)) else str(y)
    else:
        meta["date_year"] = str(meta["date_year"])

    # Apply pack context hints for fields not already present
    pack_id = _str_val(meta, "pack_id")
    ctx = _PACK_CONTEXT.get(pack_id, {})
    for field in ("culture", "medium", "region", "period"):
        if ctx.get(field) and not _str_val(meta, field):
            meta[field] = ctx[field]

    # Infer collection from source URL if not already set
    src_url = _str_val(meta, "source_url", "page_url")
    if not meta.get("collection") and src_url:
        meta["collection"] = _infer_collection(src_url)

    return meta


def find_note_path(
    meta: dict,
    folder: pathlib.Path,
    date_str: str,
    rank: int,
    root_dir: pathlib.Path = ROOT_DIR,
) -> pathlib.Path:
    images_dir = root_dir / "obsidian" / "images"
    record_id = _str_val(meta, "record_id")
    if record_id:
        return images_dir / f"{record_id}.md"
    slug = re.sub(r"^\d{2}-", "", folder.name)[:60]
    return images_dir / f"social_{date_str}_{rank:02d}_{slug}.md"


def _parse_existing_note(path: pathlib.Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_str) for an existing note, or ({}, "")."""
    if not path.is_file():
        return {}, ""
    try:
        import frontmatter as _fm
        post = _fm.load(str(path))
        return dict(post.metadata), post.content
    except Exception:
        pass
    # Manual fallback
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml
        fm: dict = yaml.safe_load(parts[1]) or {}
    except Exception:
        fm = {}
    return fm, parts[2].strip()


def _yaml_value(v: Any) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, list):
        return "[" + ", ".join(str(i) for i in v) + "]"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return '""'
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_note(path: pathlib.Path, fm: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in fm.items():
        lines.append(f"{key}: {_yaml_value(value)}")
    lines.append("---")
    content = "\n".join(lines) + "\n\n" + body.lstrip("\n")
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Frontmatter construction
# ---------------------------------------------------------------------------

def _build_frontmatter(meta: dict, existing_fm: dict) -> dict:
    def _pick(key: str, *alt_keys: str, default: str = "Unknown") -> str:
        for k in (key, *alt_keys):
            v = existing_fm.get(k)
            if v and str(v).strip() not in ("", "Unknown"):
                return str(v).strip()
        for k in (key, *alt_keys):
            v = meta.get(k)
            if v and str(v).strip() not in ("", "Unknown"):
                return str(v).strip()
        return default

    fm: dict[str, Any] = {}
    fm["type"] = existing_fm.get("type") or "social-artwork"

    record_id = _str_val(meta, "record_id")
    if record_id:
        fm["record_id"] = record_id
    source_id = _str_val(meta, "source_id")
    if source_id:
        fm["source_id"] = source_id

    fm["title"] = _pick("title")
    fm["artist"] = _pick("artist")
    fm["date_year"] = _pick("date_year", "date_created")
    date_raw = _str_val(meta, "date_raw")
    if date_raw:
        fm["date_raw"] = date_raw
    fm["period"] = _pick("period")
    fm["culture"] = _pick("culture")
    fm["region"] = _pick("region")
    fm["medium"] = _pick("medium")
    fm["materials"] = _pick("materials")

    src_url = _str_val(meta, "source_url", "page_url")
    if src_url:
        fm["source_url"] = src_url
    img_url = _str_val(meta, "image_url", "direct_image_url")
    if img_url:
        fm["image_url"] = img_url
    local_img = _str_val(meta, "local_image_path", "image_source_path")
    if local_img:
        fm["local_image_path"] = local_img

    pack_id = _str_val(meta, "pack_id")
    if pack_id:
        fm["pack_id"] = pack_id
    candidate_id = _str_val(meta, "candidate_id")
    if candidate_id:
        fm["candidate_id"] = candidate_id
    social_folder = _str_val(meta, "social_queue_folder")
    if social_folder:
        fm["social_queue_folder"] = social_folder

    if meta.get("license"):
        fm["license"] = str(meta["license"])

    # Preserve posting state
    fm["posted"] = existing_fm.get("posted") or meta.get("posted") or False
    fm["social_post_url"] = existing_fm.get("social_post_url") or meta.get("social_post_url") or ""
    fm["google_drive_image_url"] = (
        existing_fm.get("google_drive_image_url") or meta.get("google_drive_image_url") or ""
    )

    tags = list(existing_fm.get("tags") or [])
    if "social-artwork" not in tags:
        tags.append("social-artwork")
    fm["tags"] = tags

    return fm


# ---------------------------------------------------------------------------
# Historical context (metadata-derived only)
# ---------------------------------------------------------------------------

def _build_historical_context(meta: dict) -> str:
    title = _str_val(meta, "title")
    artist = _str_val(meta, "artist")
    date_year = _str_val(meta, "date_year")
    date_raw = _str_val(meta, "date_raw")
    period = _str_val(meta, "period")
    culture = _str_val(meta, "culture")
    medium = _str_val(meta, "medium")
    collection = _str_val(meta, "collection") or _infer_collection(_str_val(meta, "source_url", "page_url"))
    license_text = _str_val(meta, "license")

    parts: list[str] = []

    id_clause = f"*{title}*" if title else "this work"
    artist_clause = ""
    if artist and artist.lower() not in ("unknown", "anonymous"):
        artist_clause = f" by {artist}"
    parts.append(f"The source metadata identifies {id_clause}{artist_clause}.")

    ctx: list[str] = []
    date_clause = date_raw or (f"c. {date_year}" if date_year else "")
    if date_clause:
        ctx.append(f"dated {date_clause}")
    if period and period.lower() != "unknown":
        ctx.append(f"in the {period}")
    if culture and culture.lower() != "unknown":
        ctx.append(f"from the {culture} tradition")
    if ctx:
        parts.append("The work is " + ", ".join(ctx) + ".")

    if medium and medium.lower() != "unknown":
        parts.append(f"The source metadata notes the medium as: {medium}.")
    if collection:
        parts.append(f"Source collection: {collection}.")
    if license_text:
        parts.append(f"License: {license_text}.")

    parts.append(
        "All period, cultural, and medium attributions above are source-derived "
        "labels; treat as provisional pending independent art-historical verification."
    )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Body construction
# ---------------------------------------------------------------------------

def _extract_section(body: str, marker: str) -> str:
    """Extract section content between marker and the next ## header."""
    if marker not in body:
        return ""
    _, _, rest = body.partition(marker)
    m = re.search(r"\n## ", rest)
    return (rest[: m.start()].strip() if m else rest.strip())


def _build_body(
    meta: dict,
    fm: dict,
    caption_text: str,
    post_text: str,
    existing_body: str,
) -> str:
    title = fm.get("title", "Unknown")
    artist = fm.get("artist", "Unknown")
    date_year = fm.get("date_year", "Unknown")
    medium = fm.get("medium", "Unknown")
    src_url = fm.get("source_url", "")
    collection = _infer_collection(src_url)
    src_display = collection or src_url or "—"

    # Header
    header = f"""# {title}

**Artist**: {artist}
**Date**: {date_year}
**Medium**: {medium}
**Source**: [{src_display}]({src_url})
**Pack**: {fm.get("pack_id", "—")}
"""

    # Source Metadata table
    date_raw = _str_val(meta, "date_raw")
    rows = [
        ("Record ID", f'`{fm.get("record_id", "—")}`'),
        ("Candidate ID", f'`{fm.get("candidate_id", "—")}`'),
        ("Source ID", f'`{fm.get("source_id", "—")}`'),
        ("Artist", artist),
        ("Date / Year", date_year + (f" ({date_raw})" if date_raw else "")),
        ("Period", fm.get("period", "Unknown")),
        ("Culture", fm.get("culture", "Unknown")),
        ("Region", fm.get("region", "Unknown")),
        ("Medium", medium),
        ("License", _str_val(meta, "license") or "—"),
        ("Pack", fm.get("pack_id") or "—"),
        ("Combined Score", str(meta.get("combined_score") or "—")),
    ]
    table = "| Field | Value |\n|-------|-------|\n" + "\n".join(f"| {f} | {v} |" for f, v in rows)
    source_section = "## Source Metadata\n\n" + table

    # Visual Description
    visual = caption_text or _str_val(meta, "caption") or "(not recorded)"
    visual_section = f"## Visual Description\n\n{visual}"

    # Historical Context (preserve if already written)
    existing_hist = _extract_section(existing_body, _HISTORICAL_CTX_MARKER)
    hist_text = (
        existing_hist
        if existing_hist and existing_hist != "(not recorded)"
        else _build_historical_context(meta)
    )
    hist_section = f"{_HISTORICAL_CTX_MARKER}\n\n{hist_text}"

    # Medium / Materials
    medium_val = medium if medium != "Unknown" else "Not recorded in source metadata"
    materials_val = fm.get("materials", "Unknown")
    if not materials_val or materials_val == "Unknown":
        materials_val = "Not recorded in source metadata"
    medium_section = (
        "## Medium / Materials / Pigments\n\n"
        f"**Medium**: {medium_val}\n"
        f"**Materials**: {materials_val}\n\n"
        "> Do not assert specific pigments, binding agents, or techniques "
        "unless stated in source metadata."
    )

    # Symbolic Motifs (preserve if present)
    existing_motifs = _extract_section(existing_body, "## Symbolic Motifs")
    if existing_motifs and existing_motifs != "*(Fill in after research — do not assert without source grounding)*":
        motifs_section = f"## Symbolic Motifs\n\n{existing_motifs}"
    else:
        motifs_section = "## Symbolic Motifs\n\n*(Fill in after research — do not assert without source grounding)*"

    # Recurrence (preserve if present)
    existing_rec = _extract_section(existing_body, "## Recurrence") or _extract_section(existing_body, "## Related / Recurrence")
    if existing_rec and "No recurrence matches" not in existing_rec and existing_rec != "*(Fill in from recurrence checker — links to related records)*":
        rec_section = f"## Related / Recurrence\n\n{existing_rec}"
    else:
        rec_section = "## Related / Recurrence\n\n*(Fill in from recurrence checker — links to related records)*"

    # Social Caption Drafts
    draft = post_text or "(not yet generated)"
    caption_section = f"{_SOCIAL_CAPTION_MARKER}\n\n```\n{draft}\n```"

    # Posting Log (preserve existing entries)
    existing_log = _extract_section(existing_body, _POSTING_LOG_MARKER)
    log_content = existing_log if existing_log and existing_log != "*(not yet posted)*" else "*(not yet posted)*"
    log_section = f"{_POSTING_LOG_MARKER}\n\n{log_content}"

    # Preserve Pass 1 / Pass 2 content from the ingest pipeline if present
    pass_block = ""
    if "## Pass 1" in existing_body:
        _, _, rest = existing_body.partition("## Pass 1")
        # Stop before any social enrichment sections we're about to write
        stop_markers = [_HISTORICAL_CTX_MARKER, "## Source Metadata", _SOCIAL_CAPTION_MARKER]
        end = len(rest)
        for stop in stop_markers:
            pos = rest.find("\n" + stop)
            if pos != -1 and pos < end:
                end = pos
        pass_block = "\n---\n\n## Pass 1" + rest[:end].rstrip()

    body = "\n---\n\n".join([
        header.rstrip(),
        source_section,
        visual_section,
        hist_section,
        medium_section,
        motifs_section,
        rec_section,
        caption_section,
        log_section,
    ]) + "\n"

    if pass_block:
        body += pass_block + "\n"

    return body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_folder(
    folder: pathlib.Path,
    date_str: str,
    rank: int,
    root_dir: pathlib.Path = ROOT_DIR,
    dry_run: bool = True,
) -> dict:
    """Create or update the Obsidian note for a queue folder.

    Returns: {folder, note_path, action, dry_run, error?}
    """
    result: dict[str, object] = {"folder": str(folder), "dry_run": dry_run}

    meta = _load_all_metadata(folder, root_dir)
    if not meta:
        result["action"] = "skipped"
        result["reason"] = "no metadata.json"
        return result

    meta["social_queue_folder"] = str(folder)
    caption_text = _read_text(folder / "caption.txt")
    post_text = _read_text(folder / "post.txt")

    note_path = find_note_path(meta, folder, date_str, rank, root_dir)
    result["note_path"] = str(note_path)

    existing_fm, existing_body = _parse_existing_note(note_path)
    result["action"] = "update" if note_path.is_file() else "create"

    fm = _build_frontmatter(meta, existing_fm)
    body = _build_body(meta, fm, caption_text, post_text, existing_body)

    if not dry_run:
        try:
            _write_note(note_path, fm, body)
        except Exception as exc:
            result["action"] = "error"
            result["error"] = str(exc)

    return result


def append_posting_log_entry(
    folder: pathlib.Path,
    entry: dict,
    post_text: str,
    drive_url: str = "",
    root_dir: pathlib.Path = ROOT_DIR,
) -> bool:
    """Append a posting log entry to the note for this queue folder.

    Called by post_daily_queue.py after a successful post.
    Returns True if note was updated, False if not found or failed.
    """
    meta = _read_json(folder / "metadata.json")
    rank = int(entry.get("rank") or 0)
    date_str = str(entry.get("date") or "")

    note_path = find_note_path(meta, folder, date_str, rank, root_dir)
    if not note_path.is_file():
        return False

    tweet_id = str(entry.get("tweet_id") or "—")
    tweet_url = str(entry.get("social_post_url") or "")
    posted_at = str(entry.get("posted_at") or "")
    pack_id = str(entry.get("pack_id") or "")

    log_entry = (
        f"\n### Posted {posted_at}\n"
        f"- **Tweet ID**: `{tweet_id}`\n"
        f"- **X URL**: {tweet_url or '—'}\n"
        f"- **Pack**: {pack_id or '—'}\n"
        f"- **Rank**: {rank}\n"
        f"- **Drive image URL**: {drive_url or '(pending)'}\n"
        f"- **Caption**:\n  ```\n  {post_text[:280]}\n  ```\n"
    )

    try:
        text = note_path.read_text(encoding="utf-8")

        # Update frontmatter fields in place
        if tweet_url:
            text = re.sub(r'^posted:\s*(false|"false")', 'posted: true', text, flags=re.MULTILINE)
            text = re.sub(r'^social_post_url:\s*"[^"]*"', f'social_post_url: "{tweet_url}"', text, flags=re.MULTILINE)
        if drive_url:
            text = re.sub(r'^google_drive_image_url:\s*"[^"]*"', f'google_drive_image_url: "{drive_url}"', text, flags=re.MULTILINE)

        # Insert into Posting Log section
        if "*(not yet posted)*" in text:
            text = text.replace("*(not yet posted)*", log_entry.strip())
        elif _POSTING_LOG_MARKER in text:
            text = text.replace(_POSTING_LOG_MARKER, _POSTING_LOG_MARKER + log_entry, 1)
        else:
            text = text.rstrip() + f"\n\n{_POSTING_LOG_MARKER}\n{log_entry}"

        note_path.write_text(text, encoding="utf-8")
        return True

    except Exception as exc:
        print(f"[obsidian] failed to update posting log in {note_path.name}: {exc}")
        return False
