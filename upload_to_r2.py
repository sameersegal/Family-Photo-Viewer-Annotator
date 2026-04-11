"""
Upload family photos to Cloudflare R2 and generate thumbnails.

Reads images from ./images/, generates 400px-wide JPEG thumbnails, uploads
originals to full/ and thumbnails to thumbs/ in an R2 bucket, produces a
manifest.json listing every uploaded image, and saves thumbnails locally
to ./thumbs/ for local development.

Already-uploaded files are tracked in .upload_state.json and skipped on
subsequent runs, so the script is safe to re-run (resumable).

Setup
-----
1. Create an R2 bucket
   - Log in to the Cloudflare dashboard: https://dash.cloudflare.com
   - Go to R2 Object Storage > Create bucket.
   - Pick a name (e.g. "family-photos") and a location hint.

2. Generate R2 API tokens
   - In the Cloudflare dashboard go to R2 Object Storage > Manage R2 API Tokens.
   - Click "Create API token".
   - Grant "Object Read & Write" permission for the bucket you created.
   - Note the Access Key ID and Secret Access Key shown after creation.
   - Find your Account ID at the top of any Cloudflare dashboard page (right
     sidebar) or in the URL: https://dash.cloudflare.com/<ACCOUNT_ID>/...

3. Set environment variables (or edit the defaults below)
   - export R2_ACCOUNT_ID="your-account-id"
   - export R2_ACCESS_KEY_ID="your-access-key-id"
   - export R2_SECRET_ACCESS_KEY="your-secret-access-key"
   - export R2_BUCKET_NAME="family-photos"
   - export R2_PUBLIC_URL="https://pub-xxxx.r2.dev"  # optional, for manifest URLs

4. Install dependencies
   - pip install boto3 Pillow

Usage
-----
  python upload_to_r2.py              # upload all new images
  python upload_to_r2.py --dry-run    # preview what would be uploaded
"""

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit(
        "ERROR: boto3 is not installed.\n"
        "  pip install boto3"
    )

try:
    from PIL import Image
except ImportError:
    sys.exit(
        "ERROR: Pillow is not installed.\n"
        "  pip install Pillow"
    )

# ---------------------------------------------------------------------------
# Configuration -- override via environment variables
# ---------------------------------------------------------------------------
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "family-photos")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "")  # e.g. https://pub-xxxx.r2.dev

IMAGES_DIR = Path("images")
THUMBS_DIR = Path("thumbs")
STATE_FILE = Path(".upload_state.json")
MANIFEST_FILE = Path("manifest.json")

THUMB_WIDTH = 400
THUMB_QUALITY = 85
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_md5(path: Path) -> str:
    """Return the hex MD5 digest of a file (used for change detection)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state() -> dict:
    """Load the upload-state file that tracks already-uploaded images."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: Could not read {STATE_FILE}: {exc}")
    return {}


def save_state(state: dict) -> None:
    """Persist the upload-state file."""
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def collect_images() -> list[Path]:
    """Return sorted list of image files in IMAGES_DIR."""
    if not IMAGES_DIR.is_dir():
        return []
    images = [
        p for p in sorted(IMAGES_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return images


def generate_thumbnail(image_path: Path, thumb_path: Path) -> None:
    """Create a THUMB_WIDTH-px-wide JPEG thumbnail preserving aspect ratio."""
    with Image.open(image_path) as img:
        # Handle EXIF orientation so the thumbnail is right-side-up.
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        w, h = img.size
        if w <= THUMB_WIDTH:
            # Image is already small enough; just re-save as JPEG.
            new_size = (w, h)
        else:
            ratio = THUMB_WIDTH / w
            new_size = (THUMB_WIDTH, round(h * ratio))

        resized = img.resize(new_size, Image.LANCZOS)

        # Convert to RGB if necessary (e.g. RGBA PNGs, palette images).
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")

        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        resized.save(thumb_path, "JPEG", quality=THUMB_QUALITY)


def thumbnail_to_bytes(image_path: Path) -> bytes:
    """Generate a thumbnail and return it as in-memory JPEG bytes."""
    buf = io.BytesIO()
    with Image.open(image_path) as img:
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        w, h = img.size
        if w > THUMB_WIDTH:
            ratio = THUMB_WIDTH / w
            new_size = (THUMB_WIDTH, round(h * ratio))
        else:
            new_size = (w, h)

        resized = img.resize(new_size, Image.LANCZOS)
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")
        resized.save(buf, "JPEG", quality=THUMB_QUALITY)

    return buf.getvalue()


def content_type_for(path: Path) -> str:
    """Return a reasonable Content-Type for an image file."""
    ext = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }
    return mapping.get(ext, "application/octet-stream")


def build_manifest(state: dict) -> dict:
    """Build the manifest.json structure from upload state."""
    images = []
    for filename, info in sorted(state.items()):
        entry = {
            "filename": filename,
            "full_key": f"full/{filename}",
            "thumb_key": f"thumbs/{filename}",
        }
        if R2_PUBLIC_URL:
            base = R2_PUBLIC_URL.rstrip("/")
            entry["full_url"] = f"{base}/full/{filename}"
            entry["thumb_url"] = f"{base}/thumbs/{filename}"
        if "md5" in info:
            entry["md5"] = info["md5"]
        images.append(entry)
    return {"images": images, "count": len(images)}


def get_s3_client():
    """Create and return a boto3 S3 client configured for Cloudflare R2."""
    endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
        region_name="auto",
    )


def upload_bytes(s3, key: str, data: bytes, content_type: str) -> None:
    """Upload raw bytes to the configured R2 bucket."""
    s3.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def upload_file(s3, key: str, filepath: Path, content_type: str) -> None:
    """Upload a local file to the configured R2 bucket."""
    s3.upload_file(
        str(filepath),
        R2_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": content_type},
    )


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload family photos to Cloudflare R2 and generate thumbnails."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be uploaded without making any changes.",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    # --- Validate configuration (unless dry-run) --------------------------
    if not dry_run:
        missing = []
        if not R2_ACCOUNT_ID:
            missing.append("R2_ACCOUNT_ID")
        if not R2_ACCESS_KEY_ID:
            missing.append("R2_ACCESS_KEY_ID")
        if not R2_SECRET_ACCESS_KEY:
            missing.append("R2_SECRET_ACCESS_KEY")
        if missing:
            print("ERROR: The following environment variables must be set:")
            for var in missing:
                print(f"  - {var}")
            print("\nSee the docstring at the top of this script for setup instructions.")
            sys.exit(1)

    # --- Collect images ----------------------------------------------------
    images = collect_images()
    if not images:
        print(f"No images found in ./{IMAGES_DIR}/")
        print("Place your photos there and run again.")
        sys.exit(0)

    print(f"Found {len(images)} image(s) in ./{IMAGES_DIR}/")

    # --- Determine work to do (resumption) --------------------------------
    state = load_state()
    to_upload: list[tuple[Path, str]] = []  # (path, md5)

    for img_path in images:
        md5 = file_md5(img_path)
        filename = img_path.name
        prev = state.get(filename)
        if prev and prev.get("md5") == md5:
            continue  # already uploaded and unchanged
        to_upload.append((img_path, md5))

    skipped = len(images) - len(to_upload)
    if skipped:
        print(f"Skipping {skipped} already-uploaded image(s).")

    if not to_upload:
        print("Everything is up to date. Nothing to upload.")
        # Still regenerate manifest in case config changed.
        if not dry_run:
            manifest = build_manifest(state)
            MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"Manifest written to {MANIFEST_FILE} ({manifest['count']} images).")
        sys.exit(0)

    # --- Dry-run report ----------------------------------------------------
    if dry_run:
        print(f"\n[DRY RUN] Would upload {len(to_upload)} image(s):\n")
        for img_path, md5 in to_upload:
            filename = img_path.name
            size_kb = img_path.stat().st_size / 1024
            action = "UPDATE" if filename in state else "NEW"
            print(f"  [{action}] {filename}  ({size_kb:.1f} KB, md5={md5[:8]}...)")
            print(f"         -> full/{filename}")
            print(f"         -> thumbs/{filename}")
        print(f"\nTotal: {len(to_upload)} image(s) to upload, {skipped} skipped.")
        print("Run without --dry-run to execute.")
        sys.exit(0)

    # --- Create S3 client --------------------------------------------------
    print(f"\nConnecting to R2 (bucket: {R2_BUCKET_NAME})...")
    try:
        s3 = get_s3_client()
        # Quick connectivity check.
        s3.head_bucket(Bucket=R2_BUCKET_NAME)
        print("Connected successfully.")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "404" or code == "NoSuchBucket":
            print(f"ERROR: Bucket '{R2_BUCKET_NAME}' does not exist.")
        elif code == "403":
            print("ERROR: Access denied. Check your R2 API credentials.")
        else:
            print(f"ERROR: Could not connect to R2: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: Could not connect to R2: {exc}")
        sys.exit(1)

    # --- Ensure local thumbs directory exists ------------------------------
    THUMBS_DIR.mkdir(exist_ok=True)

    # --- Upload loop -------------------------------------------------------
    total = len(to_upload)
    success = 0
    failed = 0
    start_time = time.time()

    for idx, (img_path, md5) in enumerate(to_upload, 1):
        filename = img_path.name
        thumb_filename = Path(filename).stem + ".jpg"
        full_key = f"full/{filename}"
        thumb_key = f"thumbs/{thumb_filename}"
        ct = content_type_for(img_path)

        print(f"\n[{idx}/{total}] {filename}")

        try:
            # 1. Generate thumbnail locally.
            local_thumb = THUMBS_DIR / thumb_filename
            print(f"  Generating thumbnail -> ./{local_thumb}")
            generate_thumbnail(img_path, local_thumb)

            # 2. Upload original.
            size_kb = img_path.stat().st_size / 1024
            print(f"  Uploading original   -> {full_key}  ({size_kb:.1f} KB)")
            upload_file(s3, full_key, img_path, ct)

            # 3. Upload thumbnail.
            thumb_size_kb = local_thumb.stat().st_size / 1024
            print(f"  Uploading thumbnail  -> {thumb_key}  ({thumb_size_kb:.1f} KB)")
            upload_file(s3, thumb_key, local_thumb, "image/jpeg")

            # 4. Record success.
            state[filename] = {"md5": md5, "thumb": thumb_filename}
            save_state(state)
            success += 1

        except ClientError as exc:
            print(f"  ERROR (R2): {exc}")
            failed += 1
        except OSError as exc:
            print(f"  ERROR (I/O): {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed += 1

    elapsed = time.time() - start_time

    # --- Generate manifest ------------------------------------------------
    manifest = build_manifest(state)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")

    # Also upload manifest to R2.
    try:
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        upload_bytes(s3, "manifest.json", manifest_bytes, "application/json")
        print(f"\nManifest uploaded to R2 (manifest.json) and saved locally ({MANIFEST_FILE}).")
    except Exception as exc:
        print(f"\nWARNING: Could not upload manifest to R2: {exc}")
        print(f"Local manifest saved to {MANIFEST_FILE}.")

    # --- Summary ----------------------------------------------------------
    print(f"\n{'=' * 50}")
    print(f"Upload complete in {elapsed:.1f}s")
    print(f"  Succeeded : {success}")
    print(f"  Failed    : {failed}")
    print(f"  Skipped   : {skipped}")
    print(f"  Total     : {manifest['count']} image(s) in manifest")
    print(f"  Thumbnails: ./{THUMBS_DIR}/")

    if R2_PUBLIC_URL:
        print(f"  Public URL: {R2_PUBLIC_URL.rstrip('/')}/manifest.json")

    if failed:
        print(f"\n{failed} upload(s) failed. Re-run to retry them.")
        sys.exit(1)


if __name__ == "__main__":
    main()
