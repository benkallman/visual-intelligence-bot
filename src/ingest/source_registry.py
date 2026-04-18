import json
import os
from urllib.parse import urlparse

REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "sources", "approved_sources.json"
)


def load_approved_sources() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        return {"sources": []}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_source_url(url: str, registry: dict) -> bool:
    """Return True only if the URL's base domain matches an active approved source."""
    parsed = urlparse(url)
    incoming_netloc = parsed.netloc.lower().lstrip("www.")
    for source in registry.get("sources", []):
        if not source.get("active", False):
            continue
        registered = urlparse(source["base_url"]).netloc.lower().lstrip("www.")
        if incoming_netloc == registered or incoming_netloc.endswith("." + registered):
            return True
    return False
