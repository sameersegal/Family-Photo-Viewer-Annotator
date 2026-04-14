#!/usr/bin/env python3
"""
Face Detection + Embedding — Pass A of the local-AI annotation pipeline.

Runs InsightFace (buffalo_l: RetinaFace detector + ArcFace R100 embedder)
over every image in ./images/ and writes one record per detected face to
faces.json. Each record contains the 512-d embedding that powers the
clustering step (faces_cluster.py).

Prerequisites:
    pip install -r requirements-local.txt

    GPU inference is used automatically if onnxruntime-gpu is installed
    and CUDA is available. Falls back to CPU otherwise (slower but works).

Usage:
    python scripts/faces_detect.py                   # detect on all images
    python scripts/faces_detect.py --limit 50        # first N images only
    python scripts/faces_detect.py --min-size 40     # drop faces smaller than
                                                     # 40px on the short side
    python scripts/faces_detect.py --det-thresh 0.6  # detector confidence floor
    python scripts/faces_detect.py --force           # re-detect all (default:
                                                     # skip images already in
                                                     # faces.json)

Outputs:
    faces.json          — {face_id: {photo, bbox, det_score, embedding}}
    .faces_state.json   — {photos_done: [...]} for resumption
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMAGES_DIR = Path("images")
FACES_FILE = Path("faces.json")
STATE_FILE = Path(".faces_state.json")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

# InsightFace model pack. buffalo_l is ~300 MB, uses RetinaFace-R50 +
# ArcFace-R100 → 512-d embeddings. Good accuracy/speed tradeoff.
MODEL_PACK = "buffalo_l"

# Detector input resolution. Larger = catches smaller faces but slower.
# 640 is the InsightFace default; bump to 960 or 1280 if your archive has
# lots of wide group shots with tiny faces.
DET_SIZE = (640, 640)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_image_files() -> list[Path]:
    if not IMAGES_DIR.exists():
        print(f"ERROR: {IMAGES_DIR}/ directory not found.")
        sys.exit(1)
    return sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )


def load_faces() -> dict:
    if FACES_FILE.exists():
        try:
            return json.loads(FACES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not read {FACES_FILE}: {e}")
    return {}


def save_faces(faces: dict) -> None:
    # faces.json can get large (thousands of 512-d vectors); write compact.
    FACES_FILE.write_text(
        json.dumps(faces, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"photos_done": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )


def make_face_id(photo_name: str, bbox: list, det_score: float) -> str:
    """Stable face_id derived from photo + bbox, so re-runs idempotent."""
    payload = f"{photo_name}|{bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}"
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"face_{h}"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m {s:.0f}s"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Detect + embed faces with InsightFace (buffalo_l).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N images (for smoke tests).")
    parser.add_argument("--min-size", type=int, default=32,
                        help="Drop faces whose shorter bbox side is below this "
                             "(pixels). Default: 32.")
    parser.add_argument("--det-thresh", type=float, default=0.5,
                        help="Detector confidence floor, 0..1. Default: 0.5.")
    parser.add_argument("--det-size", type=int, default=640,
                        help="Detector input resolution (square). Default: 640. "
                             "Bump to 960/1280 for wide group shots.")
    parser.add_argument("--force", action="store_true",
                        help="Re-detect even for photos already processed.")
    parser.add_argument("--cpu", action="store_true",
                        help="Force CPU execution (otherwise tries GPU first).")
    args = parser.parse_args()

    # Lazy import — these are the heavy local-AI deps.
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except ImportError as e:
        print(f"ERROR: missing dependency ({e.name}).")
        print("Install the local-AI extras with:")
        print("  pip install -r requirements-local.txt")
        sys.exit(1)

    images = get_image_files()
    if args.limit:
        images = images[: args.limit]

    faces = {} if args.force else load_faces()
    state = {"photos_done": []} if args.force else load_state()
    done_set = set(state["photos_done"])

    remaining = [p for p in images if p.name not in done_set]

    print(f"{'=' * 60}")
    print(f"  Face Detection + Embedding (InsightFace buffalo_l)")
    print(f"{'=' * 60}")
    print(f"  Total images:      {len(images)}")
    print(f"  Already processed: {len(images) - len(remaining)}")
    print(f"  To process:        {len(remaining)}")
    print(f"  Existing faces:    {len(faces)}")
    print()

    if not remaining:
        print("Nothing to do. Use --force to re-run.")
        return

    # ------------------------------------------------------------------
    # Init model
    # ------------------------------------------------------------------
    providers = (
        ["CPUExecutionProvider"]
        if args.cpu
        else ["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    print(f"  Loading {MODEL_PACK} (providers: {providers})...")

    app = FaceAnalysis(name=MODEL_PACK, providers=providers)
    app.prepare(ctx_id=0 if not args.cpu else -1,
                det_size=(args.det_size, args.det_size),
                det_thresh=args.det_thresh)
    print("  Model ready.")
    print()

    # ------------------------------------------------------------------
    # Detect loop
    # ------------------------------------------------------------------
    start = time.time()
    new_faces = 0
    skipped_small = 0
    errors = 0
    save_every = 25  # flush to disk periodically

    for i, img_path in enumerate(remaining, 1):
        elapsed = time.time() - start
        if i > 1:
            avg = elapsed / (i - 1)
            eta = avg * (len(remaining) - i + 1)
            eta_str = f" | ETA: {format_duration(eta)}"
        else:
            eta_str = ""

        try:
            img = cv2.imread(str(img_path))
            if img is None:
                print(f"[{i}/{len(remaining)}] {img_path.name}: could not decode, skipping")
                errors += 1
                continue

            results = app.get(img)
            kept = 0
            for face in results:
                x1, y1, x2, y2 = face.bbox.astype(float).tolist()
                w, h = x2 - x1, y2 - y1
                if min(w, h) < args.min_size:
                    skipped_small += 1
                    continue

                bbox = [round(x1, 2), round(y1, 2), round(w, 2), round(h, 2)]
                fid = make_face_id(img_path.name, bbox, float(face.det_score))

                faces[fid] = {
                    "photo": img_path.name,
                    "bbox": bbox,
                    "det_score": round(float(face.det_score), 4),
                    "embedding": [round(float(v), 5) for v in face.normed_embedding],
                }
                kept += 1
                new_faces += 1

            print(f"[{i}/{len(remaining)}] {img_path.name}: {kept} face(s){eta_str}")

            done_set.add(img_path.name)

            if i % save_every == 0:
                state["photos_done"] = sorted(done_set)
                save_faces(faces)
                save_state(state)

        except KeyboardInterrupt:
            print("\nInterrupted. Saving progress...")
            state["photos_done"] = sorted(done_set)
            save_faces(faces)
            save_state(state)
            sys.exit(130)
        except Exception as e:
            print(f"[{i}/{len(remaining)}] {img_path.name}: ERROR {type(e).__name__}: {e}")
            errors += 1

    # Final flush
    state["photos_done"] = sorted(done_set)
    save_faces(faces)
    save_state(state)

    total = time.time() - start
    print()
    print(f"{'=' * 60}")
    print(f"  Done")
    print(f"{'=' * 60}")
    print(f"  New faces found:       {new_faces}")
    print(f"  Small faces dropped:   {skipped_small}")
    print(f"  Errors:                {errors}")
    print(f"  Total faces on disk:   {len(faces)}")
    print(f"  Elapsed:               {format_duration(total)}")
    print(f"  Output:                {FACES_FILE}")
    print()
    print(f"Next step:  python scripts/faces_cluster.py")


if __name__ == "__main__":
    main()
