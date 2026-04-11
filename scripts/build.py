"""
Build script for Family Photo Album.

Scans ./images/ and generates manifest.json with the image list.
Optionally generates thumbnails for local development.

Usage:
  python scripts/build.py              # Generate manifest.json
  python scripts/build.py --thumbs     # Also generate local thumbnails
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Always resolve paths relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
IMAGES_DIR = Path("images")
THUMBS_DIR = Path("thumbs")
MANIFEST_FILE = Path("manifest.json")

# Thumbnail settings
THUMB_WIDTH = 400
THUMB_QUALITY = 85


def generate_thumbnails():
    """Generate local thumbnails using Pillow."""
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: Pillow is required for thumbnails. Run: pip install Pillow")
        return False

    THUMBS_DIR.mkdir(exist_ok=True)
    images = sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )

    generated = 0
    for img_path in images:
        thumb_path = THUMBS_DIR / img_path.name
        if thumb_path.exists():
            continue

        try:
            with Image.open(img_path) as img:
                ratio = THUMB_WIDTH / img.width
                new_height = int(img.height * ratio)
                thumb = img.resize((THUMB_WIDTH, new_height), Image.LANCZOS)

                # Convert to RGB if necessary (for PNG with alpha)
                if thumb.mode in ("RGBA", "P"):
                    thumb = thumb.convert("RGB")

                thumb.save(thumb_path, "JPEG", quality=THUMB_QUALITY)
                generated += 1
        except Exception as e:
            print(f"  Warning: Could not process {img_path.name}: {e}")

    print(f"Generated {generated} new thumbnails in ./{THUMBS_DIR}/")
    return True


def main():
    if not IMAGES_DIR.exists():
        print(f"ERROR: {IMAGES_DIR}/ folder not found. Download images first.")
        print("  Run: python scripts/download_images.py <FOLDER_ID>")
        return

    images = sorted(
        f.name
        for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )

    print(f"Found {len(images)} images.")

    if not images:
        print("No images found in ./images/")
        return

    # Generate manifest.json
    manifest = {
        "images": images,
        "count": len(images),
        "generated": datetime.now(timezone.utc).isoformat(),
    }

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {MANIFEST_FILE} with {len(images)} images.")

    # Generate thumbnails if requested
    if "--thumbs" in sys.argv:
        generate_thumbnails()

    print()
    print("Next steps:")
    print("  1. python scripts/serve.py          # Start local server")
    print("  2. Open http://localhost:8765")
    if "--thumbs" not in sys.argv:
        print()
        print("  Tip: Run with --thumbs to generate local thumbnails for faster gallery loading")


if __name__ == "__main__":
    main()
