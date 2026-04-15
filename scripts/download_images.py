"""
Download all images from a Google Drive folder (recursively) into a flat local folder.

Prerequisites:
  1. Create a Google Cloud project at https://console.cloud.google.com
  2. Enable the Google Drive API
  3. Create OAuth 2.0 credentials (Desktop app) and download as credentials.json
  4. Place credentials.json in the project root directory

Usage:
  python scripts/download_images.py <FOLDER_ID>

  FOLDER_ID is the ID from the Google Drive folder URL:
  https://drive.google.com/drive/folders/<FOLDER_ID>
"""

import argparse
import os
import sys
from pathlib import Path

# Always resolve paths relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
}
OUTPUT_DIR = Path("images")


def authenticate():
    creds = None
    token_path = Path("token.json")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path("credentials.json").exists():
                print("ERROR: credentials.json not found.")
                print("Download it from Google Cloud Console (OAuth 2.0 Desktop app).")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            headless = os.environ.get("GDRIVE_HEADLESS") == "1"
            if headless:
                # Fixed port so the user can SSH-forward it:
                #   ssh -L 8765:localhost:8765 <spark>
                # Then open the printed URL in the laptop browser.
                creds = flow.run_local_server(
                    host="localhost", port=8765, open_browser=False,
                    authorization_prompt_message=(
                        "Open this URL in a browser on a machine that can reach "
                        "localhost:8765 on the Spark (use SSH port-forwarding):\n\n{url}\n"
                    ),
                )
            else:
                creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def list_files_recursive(service, folder_id):
    """Recursively list all image files in a Drive folder."""
    images = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )

        for f in response.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                images.extend(list_files_recursive(service, f["id"]))
            elif f["mimeType"] in IMAGE_MIMES:
                images.append(f)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return images


def download_file(service, file_id, dest_path):
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def main():
    parser = argparse.ArgumentParser(description="Download images from a Google Drive folder.")
    parser.add_argument("folder_id", help="Google Drive folder ID")
    parser.add_argument("--limit", type=int, default=None, help="Download only the first N images")
    args = parser.parse_args()

    folder_id = args.folder_id
    service = authenticate()

    print("Scanning Google Drive folder (recursively)...")
    images = list_files_recursive(service, folder_id)
    print(f"Found {len(images)} images.")

    if args.limit:
        images = images[: args.limit]
        print(f"Limiting to first {len(images)}.")

    if not images:
        print("No images found. Check the folder ID.")
        sys.exit(0)

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Track used filenames to handle collisions
    used_names = {}

    for i, img in enumerate(images, 1):
        name = img["name"]
        stem = Path(name).stem
        suffix = Path(name).suffix or ".jpg"

        # Handle filename collisions
        if name in used_names:
            used_names[name] += 1
            name = f"{stem}_{used_names[name]}{suffix}"
        else:
            used_names[name] = 0

        dest = OUTPUT_DIR / name
        print(f"[{i}/{len(images)}] Downloading {name}...")
        download_file(service, img["id"], dest)

    print(f"\nDone! {len(images)} images saved to ./{OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
