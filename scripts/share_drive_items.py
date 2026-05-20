"""
One-shot script: share the imagebot Drive folder and two Docs with the
service-account email as Editor, using YOUR Google account (OAuth browser flow).

Requirements
------------
1. A Google Cloud OAuth 2.0 Desktop-app client credential file.
   - Go to: https://console.cloud.google.com/apis/credentials?project=metal-sorter-496820-u8
   - "Create Credentials" → "OAuth client ID" → Application type: Desktop app
   - Download JSON → save as  scripts/oauth_client.json  (or pass --creds <path>)

2. Run once:
   python scripts\share_drive_items.py

   Your browser will open for Google sign-in. After approving, the script
   shares all three items and prints a confirmation.
"""

import argparse
import json
import pathlib
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
except ImportError:
    sys.exit(
        "Missing packages. Run:\n"
        "  .venv\\Scripts\\pip install google-auth-oauthlib google-api-python-client"
    )

SCOPES = ["https://www.googleapis.com/auth/drive"]

SERVICE_ACCOUNT_EMAIL = "imagebot-drive-logger@metal-sorter-496820-u8.iam.gserviceaccount.com"

ITEMS = [
    {
        "name": "imagebot folder",
        "id": "1M7AnHaiY6wWORQVZi_Hp5Bxr2D93ZHdP",
        "type": "folder",
    },
    {
        "name": "imagebot_post_log",
        "id": "1hUGJxxEdn-4sv6zrF-3nzjHNUj0fSn7EqDcpSpBJ0sw",
        "type": "document",
    },
    {
        "name": "imagebot_oversized_image_log",
        "id": "1yPaZbIypliUXJcoxOZ7eLSV4eHwmqO9YJHBwLh_VH8k",
        "type": "document",
    },
]

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CREDS = ROOT / "scripts" / "oauth_client.json"
TOKEN_CACHE = ROOT / "scripts" / "oauth_token.json"


def get_credentials(client_secrets_path: pathlib.Path) -> Credentials:
    creds = None
    if TOKEN_CACHE.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_CACHE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secrets_path.is_file():
                sys.exit(
                    f"\nOAuth client file not found: {client_secrets_path}\n\n"
                    "Steps to create one:\n"
                    "  1. https://console.cloud.google.com/apis/credentials"
                    "?project=metal-sorter-496820-u8\n"
                    "  2. Create Credentials → OAuth client ID → Desktop app\n"
                    "  3. Download JSON → save as scripts/oauth_client.json\n"
                    "  4. Re-run this script.\n"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_CACHE.write_text(creds.to_json(), encoding="utf-8")
        print(f"[auth] token cached at {TOKEN_CACHE}")

    return creds


def share_item(service, item: dict) -> None:
    file_id = item["id"]
    name = item["name"]
    try:
        service.permissions().create(
            fileId=file_id,
            body={
                "type": "user",
                "role": "writer",
                "emailAddress": SERVICE_ACCOUNT_EMAIL,
            },
            sendNotificationEmail=False,
            fields="id",
        ).execute()
        print(f"  [OK] {name} → Editor access granted")
    except Exception as exc:
        print(f"  [FAIL] {name}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Share Drive items with imagebot service account")
    parser.add_argument(
        "--creds",
        default=str(DEFAULT_CREDS),
        help="Path to OAuth client secrets JSON (default: scripts/oauth_client.json)",
    )
    args = parser.parse_args()

    print(f"Sharing {len(ITEMS)} items with:\n  {SERVICE_ACCOUNT_EMAIL}\n")

    creds = get_credentials(pathlib.Path(args.creds))
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    for item in ITEMS:
        share_item(service, item)

    print("\nDone. Now run:\n  python scripts\\test_drive_post_log.py --send-test")


if __name__ == "__main__":
    main()
