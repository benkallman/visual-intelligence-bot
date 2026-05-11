#!/usr/bin/env python3
"""
Diagnostic tool: check whether X/Twitter credentials are present and where
they are sourced from (environment variable, .env file, etc.).

Safe to run at any time — full secret values are never printed. Each present
credential is shown as a short masked prefix (e.g. "sk-ab…") via mask_secret,
which is a diagnostic marker only and does not expose the full value.

Output also flags whether all four credentials required by post_to_x --send
are present, and whether the optional bearer token is missing (bearer absence
does not affect posting — only read/search operations need it).

Credentials are loaded through src.utils.social_env so the resolution order
(env var > .env file) is consistent with post_to_x.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.social_env import SOCIAL_ENV_VARS, load_social_env, mask_secret

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
SEND_REQUIRED_VARS = [
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_SECRET",
]


def main() -> None:
    """Print a credential status report to stdout.

    For each present variable the masked prefix and source are shown —
    the prefix is intentionally short and safe to share in logs or bug reports.
    Exits normally regardless of credential state; callers that need a
    machine-readable result should inspect the exit code of post_to_x --send
    directly.
    """
    resolved = load_social_env(ROOT_DIR)

    present = [key for key in SOCIAL_ENV_VARS if resolved[key]["present"]]
    missing = [key for key in SOCIAL_ENV_VARS if not resolved[key]["present"]]
    # All four OAuth 1.0a credentials must be present for --send to work.
    ready_for_send = all(resolved[key]["present"] for key in SEND_REQUIRED_VARS)

    print("[social-env] Present:")
    for key in present:
        value = str(resolved[key]["value"])
        source = str(resolved[key]["source"])
        # mask_secret truncates to a short prefix — safe to print.
        print(f"  - {key}: yes ({source}, {mask_secret(value)})")

    print("[social-env] Missing:")
    if missing:
        for key in missing:
            print(f"  - {key}")
    else:
        print("  - none")

    print(f"[social-env] Ready for post_to_x --send: {'yes' if ready_for_send else 'no'}")
    if not resolved["TWITTER_BEARER_TOKEN"]["present"]:
        # Bearer token is only needed for read/search operations, not for posting.
        print("[social-env] Note: TWITTER_BEARER_TOKEN is missing; dry-run is unaffected.")


if __name__ == "__main__":
    main()
