#!/usr/bin/env python3
"""
Build local archive context from VISUAL_ARCHIVE_PATH and write data/archive/archive_context.json.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.archive.loader import ARCHIVE_CONTEXT_PATH, build_archive_context


def main() -> None:
    context = build_archive_context(force_reload=True)
    print(f"[archive-build] wrote {ARCHIVE_CONTEXT_PATH}")
    print(f"[archive-build] files_loaded={context['files_loaded']}")
    print(f"[archive-build] motifs={len(context['motifs'])}")
    print(f"[archive-build] patterns={len(context['patterns'])}")
    print(f"[archive-build] visual_principles={len(context['visual_principles'])}")


if __name__ == "__main__":
    main()
