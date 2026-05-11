"""
Load local knowledge from visual-intelligence-archive and derive prompt-safe context.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from functools import lru_cache
from typing import Any

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
DATA_ARCHIVE_DIR = os.path.join(ROOT_DIR, "data", "archive")
ARCHIVE_CONTEXT_PATH = os.path.join(DATA_ARCHIVE_DIR, "archive_context.json")
DEFAULT_VISUAL_ARCHIVE_PATH = "C:/visual-intelligence-archive"
MAX_ARCHIVE_FILE_BYTES = 500 * 1024

MOTIF_KEYWORDS = (
    "motif",
    "symbolic",
    "symbol",
    "symbolic candidate",
    "threshold",
    "gesture",
    "composition",
    "portable meaning",
)
PATTERN_KEYWORDS = (
    "pattern",
    "pattern recognition",
    "recurrence",
    "repeat",
    "repetition",
    "structural recurrence",
    "surface recurrence",
    "symbolic recurrence",
    "cross-image",
)
VISUAL_PRINCIPLE_KEYWORDS = (
    "witness",
    "description",
    "inference",
    "restraint",
    "archive context",
    "practical rule",
    "agent implication",
    "do not",
    "what to avoid",
    "what this is not",
)
STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "there", "their", "what",
    "into", "about", "when", "where", "which", "while", "through", "using",
    "image", "images", "agent", "agents", "visual", "archive", "context",
}


def _archive_path() -> str:
    return os.environ.get("VISUAL_ARCHIVE_PATH", DEFAULT_VISUAL_ARCHIVE_PATH)


def _iter_markdown_files(base_path: str) -> list[str]:
    if not os.path.isdir(base_path):
        return []
    paths: list[str] = []
    for root, _, files in os.walk(base_path):
        for name in files:
            if name.lower().endswith(".md"):
                paths.append(os.path.join(root, name))
    return sorted(paths)


def _read_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _should_load_file(path: str, text: str) -> bool:
    if os.path.getsize(path) > MAX_ARCHIVE_FILE_BYTES:
        return False
    if not text.strip():
        return False
    return True


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].lstrip()


def _extract_headings(text: str) -> list[str]:
    headings = []
    for line in _strip_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                headings.append(heading)
    return headings


def _extract_sections(text: str) -> list[dict[str, str]]:
    clean = _strip_frontmatter(text)
    sections: list[dict[str, str]] = []
    current_heading = ""
    paragraph_lines: list[str] = []
    in_code_block = False

    def flush() -> None:
        nonlocal paragraph_lines
        paragraph = " ".join(line.strip() for line in paragraph_lines if line.strip()).strip()
        paragraph_lines = []
        if not paragraph:
            return
        if paragraph.startswith("|") or paragraph.startswith("- ") or paragraph.startswith("1. "):
            return
        sections.append({"heading": current_heading, "text": paragraph})

    for raw_line in clean.splitlines():
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if stripped.startswith("#"):
            flush()
            current_heading = stripped.lstrip("#").strip()
            continue

        if not stripped:
            flush()
            continue

        paragraph_lines.append(stripped)

    flush()
    return sections


def _contains_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _normalize_entry(path: str, entry: dict[str, str]) -> dict[str, str]:
    rel_path = os.path.relpath(path, _archive_path()).replace("\\", "/")
    return {
        "file": rel_path,
        "heading": entry.get("heading", ""),
        "text": entry.get("text", ""),
    }


def _dedupe(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    output = []
    for entry in entries:
        key = (entry.get("file", ""), entry.get("heading", ""), entry.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(entry)
    return output


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _score_entry(entry: dict[str, str], reference_tokens: set[str]) -> tuple[int, int]:
    entry_text = " ".join([entry.get("heading", ""), entry.get("text", ""), entry.get("file", "")])
    entry_tokens = _tokens(entry_text)
    return len(reference_tokens & entry_tokens), len(entry_tokens)


def _select_entries(entries: list[dict[str, str]], reference_text: str, limit: int) -> list[dict[str, str]]:
    if not entries:
        return []

    reference_tokens = _tokens(reference_text)
    ranked = sorted(
        entries,
        key=lambda entry: (
            _score_entry(entry, reference_tokens)[0],
            _score_entry(entry, reference_tokens)[1],
            entry.get("heading", ""),
        ),
        reverse=True,
    )

    selected = [entry for entry in ranked if _score_entry(entry, reference_tokens)[0] > 0][:limit]
    if len(selected) < limit:
        for entry in ranked:
            if entry in selected:
                continue
            selected.append(entry)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _build_archive_context() -> dict[str, Any]:
    archive_path = _archive_path()
    files = _iter_markdown_files(archive_path)

    motifs: list[dict[str, str]] = []
    patterns: list[dict[str, str]] = []
    visual_principles: list[dict[str, str]] = []
    source_files: list[str] = []

    for path in files:
        text = _read_markdown(path)
        if not _should_load_file(path, text):
            continue

        headings = _extract_headings(text)
        sections = _extract_sections(text)
        rel_path = os.path.relpath(path, archive_path).replace("\\", "/")
        source_files.append(rel_path)

        for heading in headings:
            normalized = _normalize_entry(path, {"heading": heading, "text": heading})
            heading_text = heading.lower()
            if _contains_keywords(heading_text, MOTIF_KEYWORDS):
                motifs.append(normalized)
            if _contains_keywords(heading_text, PATTERN_KEYWORDS):
                patterns.append(normalized)
            if _contains_keywords(heading_text, VISUAL_PRINCIPLE_KEYWORDS):
                visual_principles.append(normalized)

        for section in sections:
            section_text = " ".join([section.get("heading", ""), section.get("text", "")]).lower()
            normalized = _normalize_entry(path, section)
            if _contains_keywords(section_text, MOTIF_KEYWORDS):
                motifs.append(normalized)
            if _contains_keywords(section_text, PATTERN_KEYWORDS):
                patterns.append(normalized)
            if _contains_keywords(section_text, VISUAL_PRINCIPLE_KEYWORDS):
                visual_principles.append(normalized)

    return {
        "loaded_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "files_loaded": len(source_files),
        "motifs": _dedupe(motifs),
        "patterns": _dedupe(patterns),
        "visual_principles": _dedupe(visual_principles),
        "source_files": source_files,
    }


def _write_archive_context(context: dict[str, Any]) -> str:
    os.makedirs(DATA_ARCHIVE_DIR, exist_ok=True)
    with open(ARCHIVE_CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)
    return ARCHIVE_CONTEXT_PATH


@lru_cache(maxsize=1)
def load_archive_context() -> dict[str, Any]:
    context = _build_archive_context()
    _write_archive_context(context)
    print(f"[archive] loaded {context['files_loaded']} files, {len(context['motifs'])} motifs")
    return context


def build_archive_context(force_reload: bool = False) -> dict[str, Any]:
    if force_reload:
        load_archive_context.cache_clear()
    return load_archive_context()


def get_archive_context_for_prompt(reference: Any, per_section_limit: int = 5) -> dict[str, list[dict[str, str]]]:
    context = load_archive_context()
    if isinstance(reference, list):
        reference_text = "\n".join(str(item) for item in reference)
    else:
        reference_text = str(reference or "")

    return {
        "motifs": _select_entries(context.get("motifs", []), reference_text, per_section_limit),
        "patterns": _select_entries(context.get("patterns", []), reference_text, per_section_limit),
        "visual_principles": _select_entries(
            context.get("visual_principles", []),
            reference_text,
            per_section_limit,
        ),
    }
