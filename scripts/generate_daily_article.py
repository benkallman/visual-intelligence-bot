#!/usr/bin/env python3
"""
Generate a daily article connecting a Japanese woodblock artwork to a historical
pigment/color topic.

Reads:
  exports/social/YYYY-MM-DD/  -- queue folders written by schedule_pack_future_posts.py
  data/article_topics/japanese_pigments.json  -- pigment topic summaries

Writes:
  exports/articles/YYYY-MM-DD/article.md   -- Markdown article
  exports/articles/YYYY-MM-DD/article.json -- Machine-readable metadata

The pigment topic rotates daily through a fixed list (date.toordinal() % N).
The article avoids claiming a specific artwork uses a particular pigment unless
the title, artist, or source metadata explicitly supports it.

Usage:
  python scripts/generate_daily_article.py --date today
  python scripts/generate_daily_article.py --date 2026-05-20 --pack japanese_wood_historical
  python scripts/generate_daily_article.py --date today --topic-mode pigment
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

# Windows consoles default to cp1252; reconfigure to avoid encoding errors on
# Japanese characters in pigment names and artwork titles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
SOCIAL_EXPORTS_DIR = ROOT_DIR / "exports" / "social"
ARTICLES_DIR = ROOT_DIR / "exports" / "articles"
PIGMENTS_PATH = ROOT_DIR / "data" / "article_topics" / "japanese_pigments.json"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _resolve_date(value: str) -> str:
    if value.strip().lower() == "today":
        return datetime.date.today().isoformat()
    return datetime.date.fromisoformat(value).isoformat()


# ---------------------------------------------------------------------------
# Artwork selection
# ---------------------------------------------------------------------------

def _find_artwork_folder(date_str: str, pack_id: str) -> tuple[Path, dict] | None:
    """Return (folder, metadata) for the first valid pack folder on date_str.

    Valid means: metadata.json has pack_id == pack_id AND image.jpg + post.txt exist.
    Folders are evaluated in ascending rank order.
    Returns None if no matching folder is found.
    """
    base = SOCIAL_EXPORTS_DIR / date_str
    if not base.is_dir():
        return None

    candidates: list[tuple[int, Path]] = []
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        m = re.match(r"^(\d+)-", folder.name)
        if m:
            candidates.append((int(m.group(1)), folder))

    for _rank, folder in sorted(candidates):
        meta_path = folder / "metadata.json"
        image_path = folder / "image.jpg"
        post_path = folder / "post.txt"
        if not (meta_path.is_file() and image_path.is_file() and post_path.is_file()):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("pack_id") == pack_id:
            return folder, meta

    return None


# ---------------------------------------------------------------------------
# Pigment selection
# ---------------------------------------------------------------------------

def _load_pigments() -> list[dict]:
    if not PIGMENTS_PATH.is_file():
        raise FileNotFoundError(
            f"Pigment topics file not found: {PIGMENTS_PATH}\n"
            f"Expected: data/article_topics/japanese_pigments.json"
        )
    data = json.loads(PIGMENTS_PATH.read_text(encoding="utf-8"))
    pigments = data.get("pigments")
    if not pigments:
        raise ValueError("japanese_pigments.json has no 'pigments' list.")
    return pigments


def _select_pigment(date_str: str, pigments: list[dict]) -> dict:
    """Rotate through pigments deterministically by date ordinal."""
    d = datetime.date.fromisoformat(date_str)
    return pigments[d.toordinal() % len(pigments)]


# ---------------------------------------------------------------------------
# Artwork metadata helpers
# ---------------------------------------------------------------------------

def _clean_artist(raw: str) -> str:
    """Strip 'Creator:' prefix that Wikimedia artist fields sometimes include."""
    return re.sub(r"(?i)^creator:", "", raw or "").strip()


def _period_label(year: int | None) -> str:
    if year is None:
        return "the Edo period"
    if year < 1603:
        return "the pre-Edo period"
    if year <= 1868:
        return f"the Edo period ({year})"
    if year <= 1912:
        return f"the Meiji era ({year})"
    if year <= 1926:
        return f"the Taisho era ({year})"
    return f"the early Showa period ({year})"


def _year_str(year: int | None) -> str:
    return f" ({year})" if year else ""


def _format_source_url(url: str) -> str:
    """Strip utm tracking params from URLs for cleaner display."""
    return url.split("?")[0] if url else ""


# ---------------------------------------------------------------------------
# Pigment connection
# ---------------------------------------------------------------------------

# Keywords that allow a cautious period-specific connection to be stated
_PRUSSIAN_BLUE_ARTISTS = frozenset({
    "hokusai", "hiroshige", "kuniyoshi", "kunisada", "yoshitoshi",
    "kunichika", "toyohara", "toshikata",
})

def _pigment_connection(meta: dict, pigment: dict) -> str:
    """Return a single cautious paragraph connecting artwork period to pigment context.

    Never asserts the specific pigment is present unless the title or artist
    explicitly supports the claim (e.g. Prussian blue + post-1828 Hokusai).
    """
    year = meta.get("year")
    title_lower = (meta.get("title") or "").lower()
    artist_lower = _clean_artist(meta.get("artist") or "").lower()
    pid = pigment["id"]
    period = _period_label(year)

    if pid == "prussian_blue":
        if year and year >= 1828 and any(k in artist_lower for k in _PRUSSIAN_BLUE_ARTISTS):
            return (
                f"This work dates from {period}, squarely within the era when "
                f"Prussian blue (bero-ai) had become available to Japanese printmakers. "
                f"Artists in this circle were among its earliest and most ambitious adopters, "
                f"using it to achieve the deep, graduated blues that define their most celebrated series."
            )
        if year and year >= 1828:
            return (
                f"This work dates from {period}, after Prussian blue had entered Japan's "
                f"print workshops. Whether or not this specific piece employs it, the pigment "
                f"was reshaping the available palette during these years — making intense, "
                f"lightfast blues accessible to publishers across the market."
            )
        if year and year < 1828:
            return (
                f"This work predates the arrival of Prussian blue in Japan by some years. "
                f"Blue passages in prints of this era relied on natural indigo (ai), "
                f"a pigment with its own expressive warmth but significantly less lightfastness "
                f"than the synthetic pigment that would follow."
            )
        return (
            f"The dating of this work is uncertain, so the specific pigments available "
            f"to its makers are difficult to pin down. Prussian blue arrived in Japan in "
            f"the late 1820s; before that, indigo served as the primary blue."
        )

    if pid == "indigo":
        if year and year >= 1828:
            return (
                f"This work dates from {period}. By this time Prussian blue had begun to "
                f"displace natural indigo (ai) in many workshops, though the two pigments "
                f"coexisted for decades. The specific blues in this print reflect the palette "
                f"available in the years of transition between the traditional and synthetic."
            )
        return (
            f"This work comes from {period}, when natural indigo (ai) was the primary "
            f"source of blue in Japanese prints. Its characteristic warmth and gradation "
            f"capacity shaped the visual language of the era."
        )

    if pid == "beni":
        if year and year <= 1745:
            return (
                f"This work dates from {period} — the era when safflower red (beni) "
                f"was at the center of Japanese printmaking's color vocabulary. The pinks "
                f"and reds visible today may be significantly faded from their original "
                f"intensity; beni is among the most fugitive pigments in the historic palette."
            )
        return (
            f"Safflower red (beni) was used throughout the ukiyo-e period for pinks, "
            f"reds, and cosmetic details in figure prints. Because of its extreme sensitivity "
            f"to light, the color balance in surviving prints often differs markedly from "
            f"what the artist and publisher intended at the time of printing."
        )

    if pid == "sumi":
        return (
            f"Sumi — carbon-based ink — forms the keyblock of this print as it does every "
            f"woodblock work. The precision and expressive character of the carved line, "
            f"transferred through sumi to dampened washi paper, is the structural foundation "
            f"on which all color was registered."
        )

    if pid == "mica":
        if year and 1780 <= year <= 1810:
            return (
                f"This work dates from {period}, the height of the kirazuri (mica printing) "
                f"era. Luxury prints of these decades often featured shimmering mica backgrounds "
                f"as a mark of quality and a defining visual feature of high-end bijin-ga "
                f"and actor portrait production."
            )
        return (
            f"Mica printing (kirazuri) was most closely associated with the 1790s and early "
            f"Edo period luxury print market. Works from across the Edo and Meiji periods "
            f"occasionally incorporated mica effects as part of a broader vocabulary of "
            f"costly printing techniques reserved for special editions."
        )

    # Generic connection for pigments without strong period-specific ties
    return (
        f"This work comes from {period}. The color materials of that era — "
        f"mineral pigments, plant-based dyes, and specially prepared inks — "
        f"were selected, mixed, and applied through the collaborative labor "
        f"of artist, block carver, printer, and publisher. "
        f"{pigment['name_en']} ({pigment['name_ja']}) was part of the broader "
        f"palette from which printmakers of this period drew."
    )


# ---------------------------------------------------------------------------
# Article rendering
# ---------------------------------------------------------------------------

def _render_article_md(meta: dict, pigment: dict, folder: Path, date_str: str) -> str:
    title = (meta.get("title") or "Untitled").strip().strip("'\"")
    year = meta.get("year")
    artist_raw = meta.get("artist") or ""
    artist = _clean_artist(artist_raw)
    caption = (meta.get("caption") or "").strip()
    source_url = meta.get("source_url") or meta.get("page_url") or ""
    image_url_clean = _format_source_url(meta.get("image_url") or "")
    license_text = meta.get("license") or "Public domain"
    period = _period_label(year)

    pigment_en = pigment["name_en"]
    pigment_ja = pigment["name_ja"]
    connection = _pigment_connection(meta, pigment)

    lines: list[str] = []

    # Title
    lines += [
        f"# {pigment_en}: {title}",
        "",
        f"*{date_str}*",
        "",
        "---",
        "",
    ]

    # Artwork section
    lines += [
        "## Artwork of the Day",
        "",
        f"**{title}**{_year_str(year)}",
    ]
    if artist:
        lines.append(f"**Artist:** {artist}")
    lines += [
        f"**Period:** {period.replace('the ', '').capitalize()}",
        f"**License:** {license_text}",
        "",
    ]
    if caption:
        lines += [f"*{caption}*", ""]
    if source_url:
        lines += [f"[View source on Wikimedia Commons]({source_url})", ""]
    lines += ["---", ""]

    # Pigment section
    lines += [
        f"## Color of the Day: {pigment_en} ({pigment_ja})",
        "",
        pigment["summary"],
        "",
        "---",
        "",
        "## Historical Background",
        "",
        pigment["historical_background"],
        "",
        "## How It Was Made or Sourced",
        "",
        pigment["production"],
        "",
        "## In Japanese Print Culture",
        "",
        pigment["in_print_culture"],
        "",
        "## Why This Matters Visually",
        "",
        pigment["visual_qualities"],
        "",
        "---",
        "",
    ]

    # Artwork–pigment connection
    lines += [
        "## This Artwork and This Color",
        "",
        connection,
        "",
        "---",
        "",
    ]

    # Attribution note
    lines += [
        "> **A note on attribution:** "
        + pigment["attribution_note"],
        "",
        "---",
        "",
    ]

    # Sources
    lines += ["## Sources", ""]
    if source_url:
        lines.append(f"- **Artwork:** [{title}]({source_url})")
    if image_url_clean:
        lines.append(f"- **Image file:** {image_url_clean}")
    lines += [
        "- **Pigment notes:** Compiled from art-historical scholarship on Japanese "
        "printmaking materials and pigment history. No medical or conservation claims "
        "are made. Specific pigment identification in individual artworks requires "
        "physical analysis (spectroscopy, X-ray fluorescence, or microscopy).",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(date_value: str, pack_id: str, topic_mode: str) -> None:
    date_str = _resolve_date(date_value)
    print(f"[article] date={date_str}  pack={pack_id}  topic_mode={topic_mode}")

    # Load pigment topics
    pigments = _load_pigments()

    # Find artwork for this date and pack
    result = _find_artwork_folder(date_str, pack_id)
    if result is None:
        print(f"[article] no valid folder found for pack={pack_id!r} on {date_str}")
        print(f"[article] folders must have image.jpg, post.txt, and metadata.json")
        print(
            f"[article] run: python scripts/schedule_pack_future_posts.py "
            f"--pack {pack_id} --from-date <date> --write"
        )
        sys.exit(1)

    folder, meta = result

    # Select pigment
    pigment = _select_pigment(date_str, pigments)

    print(f"[article] artwork: {(meta.get('title') or '')[:70]}")
    print(f"[article] year:    {meta.get('year') or 'unknown'}")
    print(f"[article] artist:  {_clean_artist(meta.get('artist') or '') or 'unknown'}")
    print(f"[article] pigment: {pigment['name_en']} ({pigment['name_ja']})")

    # Render article
    article_md = _render_article_md(meta, pigment, folder, date_str)

    article_json_data = {
        "date": date_str,
        "pack_id": pack_id,
        "topic_mode": topic_mode,
        "artwork": {
            "title": meta.get("title"),
            "year": meta.get("year"),
            "artist": _clean_artist(meta.get("artist") or ""),
            "source_url": meta.get("source_url") or meta.get("page_url"),
            "image_url": _format_source_url(meta.get("image_url") or ""),
            "license": meta.get("license"),
            "folder": str(folder),
        },
        "pigment": {
            "id": pigment["id"],
            "name_en": pigment["name_en"],
            "name_ja": pigment["name_ja"],
        },
        "generated_at": (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "article_chars": len(article_md),
    }

    # Write outputs
    out_dir = ARTICLES_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "article.md"
    json_path = out_dir / "article.json"

    md_path.write_text(article_md, encoding="utf-8")
    json_path.write_text(
        json.dumps(article_json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[article] written: {md_path}")
    print(f"[article] written: {json_path}")
    print(f"[article] chars:   {len(article_md)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Generate a daily article connecting a Japanese woodblock artwork "
            "to a historical pigment topic."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/generate_daily_article.py --date today
  python scripts/generate_daily_article.py --date 2026-06-01 --pack japanese_wood_historical
  python scripts/generate_daily_article.py --date today --topic-mode pigment

Output:
  exports/articles/YYYY-MM-DD/article.md
  exports/articles/YYYY-MM-DD/article.json

The pigment topic rotates daily through 10 historical Japanese pigments:
  prussian_blue, indigo, vermilion, beni, sumi, gofun,
  malachite, orpiment, mica, ochre

Captions never claim a specific pigment is present in the artwork
unless the title, artist, or source metadata explicitly supports it.
""",
    )
    parser.add_argument(
        "--date", default="today",
        help="Date to generate the article for: YYYY-MM-DD or 'today' (default: today)",
    )
    parser.add_argument(
        "--pack", default="japanese_wood_historical",
        metavar="PACK_ID",
        help=(
            "Source pack ID to select artwork from "
            "(default: japanese_wood_historical)"
        ),
    )
    parser.add_argument(
        "--topic-mode", default="pigment",
        choices=["pigment"],
        metavar="MODE",
        help="Topic mode for article content (default: pigment; currently only 'pigment' is supported)",
    )

    args = parser.parse_args()

    try:
        _resolve_date(args.date)
    except ValueError:
        parser.error(f"Invalid --date: {args.date!r} — expected YYYY-MM-DD or 'today'")

    try:
        main(
            date_value=args.date,
            pack_id=args.pack,
            topic_mode=args.topic_mode,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[article] Error: {exc}", file=sys.stderr)
        sys.exit(1)
