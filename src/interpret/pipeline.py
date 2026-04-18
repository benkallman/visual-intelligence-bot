"""
Two-pass interpretation pipeline.

Pass 1: literal description only (witness mode)
Pass 2: constrained interpretation only, grounded in Pass 1

Safety gate runs after Pass 1. If rejected, the pipeline stops and
returns a record with review_status = "safety_rejected". Pass 2 and
all downstream outputs are suppressed by the caller.

Pass 2 will not run if Pass 1 is not clean (existing governance rule).
"""

import datetime

from src.interpret.pass1 import run_pass1
from src.interpret.pass2 import run_pass2
from src.providers import ProviderUnavailableError
from src.safety import run_safety_gate

SCHEMA_VERSION = "0.1.0"


def run_two_pass_pipeline(source_record: dict) -> dict:
    record_id = source_record["source_id"].replace("src_", "rec_")
    image_url = source_record.get("image_url") or source_record["url"]

    record = {
        "record_id": record_id,
        "source_id": source_record["source_id"],
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": None,
        "model": None,
        "safety": {"gate_checked": False},
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
    try:
        pass1_result, provider, model = run_pass1(image_url)
    except ProviderUnavailableError as exc:
        record["governance"]["review_status"] = "error"
        record["governance"]["flag_reason"] = f"Provider unavailable: {exc}"
        return record

    record["pass1"] = pass1_result
    record["provider"] = provider
    record["model"] = model

    # Safety gate — runs before Pass 2 and before any output is written.
    # Gate FAILS CLOSED: uncertainty and parse errors produce safety_uncertain,
    # which suppresses all outputs identically to a hard reject.
    gate = run_safety_gate(pass1_result)
    record["safety"] = {
        "gate_checked": True,
        "safe": gate.safe,
        "uncertain": gate.uncertain,
        "matched_categories": gate.matched_categories,
        "reason": gate.reason,
        "tier": gate.tier,
    }

    if not gate.safe:
        status = "safety_uncertain" if gate.uncertain else "safety_rejected"
        record["governance"]["review_status"] = status
        record["governance"]["flag_reason"] = gate.reason
        return record

    # Pass 1 clean check (existing governance rule)
    if not record["pass1"].get("pass1_clean", False):
        record["pass2"] = _empty_pass2(blocked_reason="Pass 1 not clean")
        record["governance"]["review_status"] = "flagged"
        record["governance"]["flag_reason"] = "Pass 1 contains inference — Pass 2 blocked"
        return record

    # Pass 2
    try:
        pass2_result, _, _ = run_pass2(record["pass1"])
    except ProviderUnavailableError as exc:
        record["pass2"] = _empty_pass2(blocked_reason=f"Provider unavailable: {exc}")
        record["governance"]["review_status"] = "error"
        record["governance"]["flag_reason"] = str(exc)
        return record

    record["pass2"] = pass2_result
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
