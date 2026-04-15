#!/usr/bin/env python3
"""
Face Clustering + Montage — Pass B of the local-AI annotation pipeline.

Reads faces.json (produced by faces_detect.py), runs HDBSCAN on the 512-d
ArcFace embeddings (cosine distance), and writes:

    clusters.json              — cluster_id → {size, face_ids, photos, centroid}
    people/montage/index.html  — static viewer: N representative crops per
                                 cluster, grouped by size, so you can label
                                 each cluster in labels.yml

The montage is a single standalone HTML file that reads face crops directly
from your local ./images/ folder via <img src> + CSS object-position so we
don't have to write thousands of tiny cropped files to disk.

Prerequisites:
    pip install -r requirements-local.txt

Usage:
    python scripts/faces_cluster.py                    # default settings
    python scripts/faces_cluster.py --min-cluster 3    # min faces per cluster
    python scripts/faces_cluster.py --top 50           # show top 50 in montage
    python scripts/faces_cluster.py --samples 16       # crops per cluster
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

FACES_FILE = Path("faces.json")
CLUSTERS_FILE = Path("clusters.json")
MONTAGE_DIR = Path("people/montage")
MONTAGE_HTML = MONTAGE_DIR / "index.html"
CROPS_DIR = MONTAGE_DIR / "crops"
LABELS_TEMPLATE = Path("people/labels.yml")


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

MONTAGE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #111;
    color: #eee;
    margin: 0;
    padding: 24px;
}
h1 { margin: 0 0 8px; }
.meta { color: #888; margin-bottom: 24px; font-size: 14px; line-height: 1.6; }
.meta code { background: #222; padding: 2px 6px; border-radius: 3px; }
.cluster {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 16px;
}
.cluster h2 {
    margin: 0 0 12px;
    font-size: 16px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 12px;
}
.cluster h2 .id {
    background: #2a2a2a;
    padding: 4px 10px;
    border-radius: 3px;
    font-family: monospace;
    font-size: 13px;
    color: #ffa;
}
.cluster h2 .count { color: #888; font-weight: 400; font-size: 14px; }
.faces {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: 8px;
}
.face {
    position: relative;
    aspect-ratio: 1 / 1;
    overflow: hidden;
    border-radius: 4px;
    background: #000;
    border: 1px solid #333;
}
.face img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.face .cap {
    position: absolute;
    bottom: 0; left: 0; right: 0;
    background: rgba(0, 0, 0, 0.65);
    color: #ccc;
    font-size: 10px;
    padding: 2px 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.noise { opacity: 0.6; }
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_faces() -> dict:
    if not FACES_FILE.exists():
        print(f"ERROR: {FACES_FILE} not found. Run faces_detect.py first.")
        sys.exit(1)
    return json.loads(FACES_FILE.read_text(encoding="utf-8"))


def write_face_crop(face_id: str, photo_name: str, bbox: list) -> Path | None:
    """
    Write a square face crop to people/montage/crops/<face_id>.jpg using
    OpenCV, which reads raw pixels and therefore matches the bbox
    coordinates stored by faces_detect.py (both ignore EXIF orientation).
    Returns the crop path, or None on failure.
    """
    import cv2  # local import: heavy, only needed when generating crops

    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CROPS_DIR / f"{face_id}.jpg"
    if out_path.exists():
        return out_path

    src = Path("images") / photo_name
    img = cv2.imread(str(src))
    if img is None:
        return None

    H, W = img.shape[:2]
    x, y, w, h = bbox
    # Expand to a square centred on the bbox, with a little margin, and
    # clamp to image bounds.
    cx, cy = x + w / 2.0, y + h / 2.0
    side = max(w, h) * 1.4
    x0 = max(0, int(cx - side / 2.0))
    y0 = max(0, int(cy - side / 2.0))
    x1 = min(W, int(cx + side / 2.0))
    y1 = min(H, int(cy + side / 2.0))
    if x1 <= x0 or y1 <= y0:
        return None

    crop = img[y0:y1, x0:x1]
    cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out_path


def write_montage(clusters: list, faces: dict, top_n: int, samples: int,
                  include_noise: bool) -> None:
    MONTAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Pick the top N clusters by size. Always include noise (cluster_id = -1)
    # last if requested, so the user can scan it for missed identities.
    ranked = sorted(clusters, key=lambda c: -c["size"])
    noise = [c for c in ranked if c["id"] == "noise"]
    real = [c for c in ranked if c["id"] != "noise"][:top_n]
    if include_noise:
        real.extend(noise)

    parts = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<title>Face clusters — label me</title>",
        f"<style>{MONTAGE_CSS}</style>",
        "</head><body>",
        "<h1>Face clusters</h1>",
        "<div class='meta'>",
        f"<p>Showing top <b>{len(real)}</b> clusters by size. "
        f"Edit <code>people/labels.yml</code> to assign names — "
        f"use the <code>id</code> shown next to each cluster header.</p>",
        "<p>Face crops are approximate — they use CSS transforms on the "
        "original image, not pre-cut files. If a face looks mis-framed, "
        "the data under the hood is still correct.</p>",
        "</div>",
    ]

    for c in real:
        is_noise = c["id"] == "noise"
        face_items = c["face_ids"][:samples]
        klass = "cluster noise" if is_noise else "cluster"
        parts.append(f"<section class='{klass}'>")
        parts.append(
            f"<h2><span class='id'>{escape(c['id'])}</span>"
            f"<span class='count'>{c['size']} face(s) across "
            f"{len(set(c['photos']))} photo(s)</span></h2>"
        )
        parts.append("<div class='faces'>")
        for fid in face_items:
            face = faces.get(fid)
            if not face:
                continue
            crop_path = write_face_crop(fid, face["photo"], face["bbox"])
            if crop_path is None:
                continue
            img_src = f"crops/{crop_path.name}"
            parts.append(
                f"<div class='face'>"
                f"<img src='{escape(img_src)}' loading='lazy' alt=''>"
                f"<div class='cap'>{escape(face['photo'])}</div>"
                f"</div>"
            )
        parts.append("</div></section>")

    parts.append("</body></html>")
    MONTAGE_HTML.write_text("\n".join(parts), encoding="utf-8")


def write_labels_template(clusters: list, top_n: int) -> None:
    """Emit a starter labels.yml if one doesn't already exist."""
    if LABELS_TEMPLATE.exists():
        return  # never clobber user edits

    LABELS_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    # Include every real cluster — the montage can be truncated to top_n,
    # but there's no benefit to hiding clusters from the YAML where the
    # user does the actual labeling.
    ranked = sorted(
        [c for c in clusters if c["id"] != "noise"],
        key=lambda c: -c["size"],
    )

    lines = [
        "# labels.yml — cluster_id → person_name",
        "#",
        "# Open people/montage/index.html in a browser, look at each cluster,",
        "# and fill in the name below. Special values:",
        "#   reject       — cluster is false positives / not a real face",
        "#   skip         — leave unlabeled for now (same effect as a comment)",
        "# Multiple clusters can map to the same name (e.g. Ma as child vs adult).",
        "# Unknown / unlabeled clusters will remain 'unknown' in the pipeline.",
        "",
    ]
    for c in ranked:
        lines.append(f"{c['id']}: skip   # {c['size']} faces")
    lines.append("")
    LABELS_TEMPLATE.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cluster face embeddings with HDBSCAN and build montage.",
    )
    parser.add_argument("--min-cluster", type=int, default=3,
                        help="HDBSCAN min_cluster_size (default: 3).")
    parser.add_argument("--min-samples", type=int, default=1,
                        help="HDBSCAN min_samples (default: 1 — looser, more "
                             "clusters, fewer points go to noise).")
    parser.add_argument("--top", type=int, default=60,
                        help="Show top-N clusters in montage (default: 60).")
    parser.add_argument("--samples", type=int, default=16,
                        help="Face crops per cluster in montage (default: 16).")
    parser.add_argument("--include-noise", action="store_true",
                        help="Append the noise cluster at the end of montage.")
    args = parser.parse_args()

    try:
        import numpy as np
        import hdbscan  # noqa: F401  (imported below after numpy is available)
    except ImportError as e:
        print(f"ERROR: missing dependency ({e.name}).")
        print("  pip install -r requirements-local.txt")
        sys.exit(1)

    import hdbscan

    faces = load_faces()
    if not faces:
        print("faces.json is empty. Run faces_detect.py first.")
        sys.exit(1)

    face_ids = list(faces.keys())
    X = np.asarray([faces[fid]["embedding"] for fid in face_ids], dtype=np.float32)

    print(f"  {len(face_ids)} face embeddings loaded (dim={X.shape[1]})")
    print(f"  Running HDBSCAN "
          f"(min_cluster_size={args.min_cluster}, "
          f"min_samples={args.min_samples})...")

    # ArcFace normed_embedding is already L2-normalized, so euclidean on
    # normalized vectors is equivalent to cosine (up to a monotonic map).
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=args.min_cluster,
        min_samples=args.min_samples,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(X)

    # Group
    groups: dict = defaultdict(list)
    for fid, lab in zip(face_ids, labels):
        groups[int(lab)].append(fid)

    clusters = []
    for lab, members in groups.items():
        cid = "noise" if lab == -1 else f"cluster_{lab:04d}"
        # Sort member faces by det_score desc so the "best" ones show first.
        members.sort(key=lambda fid: -faces[fid].get("det_score", 0.0))
        photos = [faces[fid]["photo"] for fid in members]
        # Centroid for later matching / merging.
        centroid = X[[face_ids.index(fid) for fid in members[:50]]].mean(axis=0)
        # L2-normalize centroid.
        norm = float(np.linalg.norm(centroid)) or 1.0
        centroid = (centroid / norm).tolist()
        clusters.append({
            "id": cid,
            "size": len(members),
            "face_ids": members,
            "photos": photos,
            "centroid": [round(v, 5) for v in centroid],
        })

    real_count = sum(1 for c in clusters if c["id"] != "noise")
    noise_count = sum(c["size"] for c in clusters if c["id"] == "noise")

    print(f"  Clusters found: {real_count}")
    print(f"  Noise points:   {noise_count}")
    print()
    print("  Top clusters by size:")
    for c in sorted(clusters, key=lambda c: -c["size"])[:15]:
        if c["id"] == "noise":
            continue
        n_photos = len(set(c["photos"]))
        print(f"    {c['id']}  {c['size']:4d} faces  across {n_photos} photos")
    print()

    CLUSTERS_FILE.write_text(
        json.dumps({"clusters": clusters}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"  Wrote {CLUSTERS_FILE}")

    write_montage(clusters, faces, top_n=args.top, samples=args.samples,
                  include_noise=args.include_noise)
    print(f"  Wrote {MONTAGE_HTML}")

    write_labels_template(clusters, top_n=args.top)
    if LABELS_TEMPLATE.exists():
        print(f"  Labels file: {LABELS_TEMPLATE}")

    print()
    print("Next steps:")
    print(f"  1. Open {MONTAGE_HTML} in a browser")
    print(f"  2. Edit {LABELS_TEMPLATE} — assign names to cluster_ids")
    print(f"  3. Run: python scripts/faces_label.py")


if __name__ == "__main__":
    main()
