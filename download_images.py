"""
Download all images from a Google Drive folder (recursively) into a flat local folder.

Prerequisites:
  1. Create a Google Cloud project at https://console.cloud.google.com
  2. Enable the Google Drive API
  3. Create OAuth 2.0 credentials (Desktop app) and download as credentials.json
  4. Place credentials.json in this directory

Usage:
  python download_images.py <FOLDER_ID>

  FOLDER_ID is the ID from the Google Drive folder URL:
  https://drive.google.com/drive/folders/<FOLDER_ID>
"""

import os
import sys
from pathlib import Path

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
    if len(sys.argv) < 2:
        print("Usage: python download_images.py <FOLDER_ID>")
        sys.exit(1)

    folder_id = sys.argv[1]
    service = authenticate()

    print("Scanning Google Drive folder (recursively)...")
    images = list_files_recursive(service, folder_id)
    print(f"Found {len(images)} images.")

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
