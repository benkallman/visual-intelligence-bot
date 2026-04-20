import json
from pathlib import Path

data = json.loads(Path("data/sources/approved_sources.json").read_text())
for s in data["sources"]:
    status = "active" if s.get("active") else "inactive"
    print(f"[{status}] {s['name']}\n         {s['base_url']}\n")
