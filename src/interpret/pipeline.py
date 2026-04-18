"""
Two-pass interpretation pipeline.

Pass 1: literal description only (witness mode)
Pass 2: constrained interpretation only, grounded in Pass 1

Pass 2 will not run if Pass 1 is not clean.
"""

import datetime
import os
import json
import anthropic

from src.interpret.pass1 import run_pass1
from src.interpret.pass2 import run_pass2

SCHEMA_VERSION = "0.1.0"
MODEL = "claude-sonnet-4-6"


def run_two_pass_pipeline(source_record: dict) -> dict:
    record_id = source_record["source_id"].replace("src_", "rec_")
    image_url = source_record.get("image_url") or source_record["url"]

    record = {
        "record_id": record_id,
        "source_id": source_record["source_id"],
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "model": MODEL,
        "pass1": {},
        "pass2": {},
        "governance": {
            "review_status": "pending",
            "human_reviewed": False,
            "reviewed_by": None,
            "reviewed_at": None,
            "correction_notes": None,
            "flag_reason": None,
        },
    }

    # Pass 1
    record["pass1"] = run_pass1(image_url, MODEL)

    # Pass 2 — blocked if Pass 1 is not clean
    if not record["pass1"].get("pass1_clean", False):
        record["pass2"] = _empty_pass2(blocked_reason="Pass 1 not clean")
        record["governance"]["review_status"] = "flagged"
        record["governance"]["flag_reason"] = "Pass 1 contains inference — Pass 2 blocked"
        return record

    record["pass2"] = run_pass2(record["pass1"], MODEL)

    return record


def _empty_pass2(blocked_reason: str) -> dict:
    return {
        "interpretive_notes": f"[BLOCKED: {blocked_reason}]",
        "symbolic_candidates": [],
        "recurrence_references": [],
        "archive_context_used": [],
        "prohibited_inference_check": {
            "passed": False,
            "violations": [{"rule": "pipeline", "offending_text": blocked_reason}],
        },
        "uncertainty_notes": None,
        "pass2_clean": False,
    }
