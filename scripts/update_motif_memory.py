#!/usr/bin/env python3
"""
Build and write motif memory from stored records and scores.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.motifs.memory import MOTIF_MEMORY_PATH, build_motif_memory, save_motif_memory


def main() -> None:
    memory = build_motif_memory()
    save_motif_memory(memory)
    print(f"[motif-memory] wrote {MOTIF_MEMORY_PATH}")
    print(f"[motif-memory] motifs={len(memory['motifs'])}")

    by_count = sorted(memory["motifs"], key=lambda item: (-item["count"], item["label"]))[:10]
    by_rarity = sorted(memory["motifs"], key=lambda item: (-item["rarity_average"], -item["count"], item["label"]))[:10]

    print("[motif-memory] top-by-count:")
    for item in by_count:
        print(
            f"  - {item['label']}: count={item['count']} "
            f"rarity_avg={item['rarity_average']:.4f} viral_avg={item['viral_average']:.4f}"
        )

    print("[motif-memory] top-by-rarity:")
    for item in by_rarity:
        print(
            f"  - {item['label']}: rarity_avg={item['rarity_average']:.4f} "
            f"count={item['count']} viral_avg={item['viral_average']:.4f}"
        )


if __name__ == "__main__":
    main()
