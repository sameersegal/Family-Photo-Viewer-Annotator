"""
Scans the ./images/ folder and generates the final index.html
with the image list embedded.

Usage:
  python build.py
"""

import json
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
IMAGES_DIR = Path("images")
TEMPLATE = Path("index.html")
OUTPUT = Path("slideshow.html")


def main():
    if not IMAGES_DIR.exists():
        print(f"ERROR: {IMAGES_DIR}/ folder not found. Download images first.")
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

    template = TEMPLATE.read_text(encoding="utf-8")
    image_list_js = json.dumps(images)
    output = template.replace("__IMAGE_LIST__", image_list_js)

    OUTPUT.write_text(output, encoding="utf-8")
    print(f"Generated {OUTPUT} with {len(images)} images.")
    print(f"Open {OUTPUT} in Chrome and press F11 for full-screen.")


if __name__ == "__main__":
    main()
