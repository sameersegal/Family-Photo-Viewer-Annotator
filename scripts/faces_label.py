#!/usr/bin/env python3
"""
Apply cluster labels — Pass C of the local-AI annotation pipeline.

Reads people/labels.yml, looks up each cluster in clusters.json, and emits:

    photo_people.json   — {photo_name: [person_name, ...]}  (dedup'd, for
                          the Worker /api/import endpoint)
    photo_faces.json    — [{face_id, photo, bbox, person_name, det_score, source}]
                          (for D1 photo_faces upsert — bboxes + names, no
                          embeddings)

Labels can map multiple clusters to the same name (covers age variation —
"Ma at 20" and "Ma at 70" both → "Ma"). Special label values:

    reject   — cluster is false positives; faces are dropped
    skip     — leave unlabeled (same as absent)
    split    — leave unlabeled; you'll re-cluster these later

Usage:
    python scripts/faces_label.py
    python scripts/faces_label.py --dry-run
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

FACES_FILE = Path("faces.json")
CLUSTERS_FILE = Path("clusters.json")
LABELS_FILE = Path("people/labels.yml")
PHOTO_PEOPLE_FILE = Path("photo_people.json")
PHOTO_FACES_FILE = Path("photo_faces.json")

SPECIAL_VALUES = {"skip", "reject", "split", ""}


def load_yaml(path: Path) -> dict:
    """Minimal YAML loader — supports `key: value` and `# comments` only.

    Avoids adding a PyYAML dep. labels.yml is intentionally flat.
    """
    out = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            print(f"WARNING: {path}:{lineno}: expected 'key: value', got: {raw!r}")
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    parser = argparse.ArgumentParser(description="Apply labels.yml to face clusters.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary without writing output files.")
    args = parser.parse_args()

    for f in (FACES_FILE, CLUSTERS_FILE, LABELS_FILE):
        if not f.exists():
            print(f"ERROR: {f} not found. Pipeline state is incomplete.")
            sys.exit(1)

    faces = json.loads(FACES_FILE.read_text(encoding="utf-8"))
    clusters_doc = json.loads(CLUSTERS_FILE.read_text(encoding="utf-8"))
    clusters = {c["id"]: c for c in clusters_doc["clusters"]}
    labels = load_yaml(LABELS_FILE)

    unknown_ids = [k for k in labels if k not in clusters]
    if unknown_ids:
        print(f"WARNING: labels.yml refers to unknown cluster ids: {unknown_ids}")
        print("These will be ignored. Check for typos.")
        print()

    # Resolve labels → list of (cluster_id, name) pairs for clusters we'll emit.
    resolved = {}  # cluster_id → person_name
    rejected = set()
    skipped = set()
    for cid, val in labels.items():
        if cid not in clusters:
            continue
        val_lower = val.lower()
        if val_lower == "reject":
            rejected.add(cid)
        elif val_lower in {"skip", "split", ""}:
            skipped.add(cid)
        else:
            resolved[cid] = val  # preserve original casing

    # Report
    name_counts = defaultdict(int)
    for cid, name in resolved.items():
        name_counts[name] += clusters[cid]["size"]

    print(f"{'=' * 60}")
    print(f"  Label resolution")
    print(f"{'=' * 60}")
    print(f"  Labeled clusters:   {len(resolved)}")
    print(f"  Rejected clusters:  {len(rejected)}")
    print(f"  Skipped clusters:   {len(skipped)}")
    unlabeled = [cid for cid in clusters if cid != "noise"
                 and cid not in labels]
    print(f"  Unlabeled clusters: {len(unlabeled)} (not mentioned in labels.yml)")
    print()

    if name_counts:
        print("  Faces assigned per person:")
        for name, n in sorted(name_counts.items(), key=lambda x: -x[1]):
            n_clusters = sum(1 for cid, nm in resolved.items() if nm == name)
            suffix = f"  ({n_clusters} clusters merged)" if n_clusters > 1 else ""
            print(f"    {name:<30} {n:5d} faces{suffix}")
        print()

    # Build photo → set(names) map
    photo_to_names: dict = defaultdict(set)
    photo_faces_out = []

    for cid, name in resolved.items():
        cluster = clusters[cid]
        for fid in cluster["face_ids"]:
            face = faces.get(fid)
            if not face:
                continue
            photo_to_names[face["photo"]].add(name)
            photo_faces_out.append({
                "face_id": fid,
                "photo": face["photo"],
                "bbox": face["bbox"],
                "person_name": name,
                "det_score": face.get("det_score"),
                "source": "ai",
            })

    # Also emit bboxes for rejected/unlabeled faces with person_name=null,
    # so the viewer can still draw all detected boxes and the user can tag
    # unknowns from the UI. Skip rejected — those were flagged as garbage.
    for cid, cluster in clusters.items():
        if cid == "noise" or cid in resolved or cid in rejected:
            continue
        for fid in cluster["face_ids"]:
            face = faces.get(fid)
            if not face:
                continue
            photo_faces_out.append({
                "face_id": fid,
                "photo": face["photo"],
                "bbox": face["bbox"],
                "person_name": None,
                "det_score": face.get("det_score"),
                "source": "ai",
            })

    print(f"  Photos with at least one named face: {len(photo_to_names)}")
    print(f"  Total face records (named + unknown): {len(photo_faces_out)}")
    print()

    if args.dry_run:
        print("[DRY RUN] Not writing output files.")
        return

    photo_people = {photo: sorted(names)
                    for photo, names in photo_to_names.items()}
    PHOTO_PEOPLE_FILE.write_text(
        json.dumps(photo_people, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    PHOTO_FACES_FILE.write_text(
        json.dumps(photo_faces_out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Wrote {PHOTO_PEOPLE_FILE}")
    print(f"  Wrote {PHOTO_FACES_FILE}")
    print()
    print("Next step:  python scripts/annotate_local.py")
    print("            (uses photo_people.json to inject names into the VLM prompt)")


if __name__ == "__main__":
    main()
