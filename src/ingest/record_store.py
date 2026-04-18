import json
import os

RECORDS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "records")


def save_interpretation_record(record: dict) -> str:
    os.makedirs(RECORDS_DIR, exist_ok=True)
    path = os.path.join(RECORDS_DIR, f"{record['record_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def load_all_records() -> list[dict]:
    if not os.path.exists(RECORDS_DIR):
        return []
    records = []
    for fname in os.listdir(RECORDS_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(RECORDS_DIR, fname), "r", encoding="utf-8") as f:
                records.append(json.load(f))
    return records
