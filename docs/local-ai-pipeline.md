# Local-AI Annotation Pipeline

An offline, four-stage pipeline that runs on a local GPU box (e.g. an
NVIDIA DGX Spark) to produce **family-specific** photo annotations —
one that actually knows who "Ma" and "Priya" are, rather than writing
"two women in saris."

Compare to the cloud pipeline (`scripts/ai_annotate.py`), which calls
Claude on every photo with only a `family_context.md` prose blurb and
no learned face identities. That path still exists and is untouched —
this doc covers the local alternative.

## Why this exists

The cloud VLM has never seen your relatives. It can describe clothing,
decade, and setting well, but it literally cannot recognize your aunt.
Every annotation comes back generic.

Fixing that requires two capabilities the cloud VLM doesn't have:

1. **Learn identity from your archive** — 20–40 known people, each
   appearing in dozens to hundreds of photos across decades.
2. **Keep working offline** — no per-photo API cost once set up;
   re-runnable whenever you add new photos.

Both fall out naturally from a face-recognition pipeline running on
local hardware, with a small VLM doing the final scene writeup after
names have been injected into its prompt.

## Design decisions

### Faces are not a VLM problem

Small VLMs (<20B) are mediocre at open-set face identification and
can't be taught a new person cheaply. Instead we use a **dedicated
face pipeline** (InsightFace → ArcFace embeddings → HDBSCAN cluster)
and feed the resulting names to the VLM as text:

> "People identified in this photo: Ma, Priya. Describe the scene."

This single change is what makes annotations feel specific.

### Cluster-first enrollment (not seed-first)

The classic face-recognition UX asks you to upload 5–10 reference
photos per person. That's busywork when every face you care about is
already in the archive. Instead we:

1. Detect + embed every face first.
2. Cluster with HDBSCAN.
3. Show you the clusters ranked by size.
4. You label each cluster with a person's name.

**Age variation is handled by merge, not by matching.** ArcFace
embeddings drift with age, so "Ma at 20" and "Ma at 70" often land in
different clusters. Rather than fighting this, we let both exist and
map both to `Ma` in `labels.yml`. You get multi-decade recognition for
free.

### Embeddings stay local

The 512-d ArcFace embeddings live in `faces.json` on the Spark and
are never uploaded to D1. D1's `photo_faces` table stores only the
resolved bbox + person_name — the minimum the viewer needs to draw
overlays. This keeps the cloud surface small and the offline workflow
self-contained.

## Pipeline overview

```
        ./images/ (local)                  family_context.md
              │                                   │
              ▼                                   │
  ┌─────────────────────────┐                     │
  │  A. faces_detect.py     │  InsightFace        │
  │  (RetinaFace + ArcFace) │  buffalo_l          │
  └──────────┬──────────────┘                     │
             │                                    │
             ▼                                    │
         faces.json                               │
         (bbox + 512-d embedding per face)        │
             │                                    │
             ▼                                    │
  ┌─────────────────────────┐                     │
  │  B. faces_cluster.py    │  HDBSCAN            │
  │                         │  (cosine / L2-norm) │
  └──────────┬──────────────┘                     │
             │                                    │
             ▼                                    │
    clusters.json  +  people/montage/index.html   │
         +  people/labels.yml (starter template)  │
             │                                    │
             ▼                                    │
    ┌────────────────────────────┐                │
    │  HUMAN: open montage in    │                │
    │  browser, edit labels.yml  │                │
    └──────────┬─────────────────┘                │
               │                                  │
               ▼                                  │
  ┌─────────────────────────┐                     │
  │  C. faces_label.py      │  resolve merges,    │
  │                         │  rejects, unknowns  │
  └──────────┬──────────────┘                     │
             │                                    │
             ▼                                    │
     photo_people.json  +  photo_faces.json       │
             │                                    │
             ├──────────────────────────────────┐ │
             │                                  │ │
             ▼                                  ▼ ▼
  ┌─────────────────────────┐        ┌──────────────────────┐
  │  D. annotate_local.py   │◀───────│  per-photo prompt    │
  │  (Ollama + Qwen2.5-VL)  │        │  (names + EXIF +     │
  └──────────┬──────────────┘        │   family context)    │
             │                       └──────────────────────┘
             ▼
       annotations.json
             │
             ▼
     POST /api/import  ─────▶  D1 (photos + photo_people)
```

## Stage A — `faces_detect.py`

**What it does:** Runs InsightFace's `buffalo_l` model pack
(RetinaFace detector + ArcFace R100 embedder) over every image.
For each detected face it records the bounding box, detector
confidence, and a 512-d L2-normalized embedding.

**Output:** `faces.json`, keyed by a stable `face_id` derived from
`sha1(photo_name | bbox)`. Re-runs are idempotent — if you drop new
photos into `./images/`, a second run picks up only the new ones
(tracked in `.faces_state.json`).

**Knobs worth knowing:**

| Flag | Default | When to change |
|---|---|---|
| `--min-size` | 32 px | Raise to drop background faces in crowd shots; lower for tiny children in wide group photos |
| `--det-thresh` | 0.5 | Lower to catch blurry faces; raise to cut false positives |
| `--det-size` | 640 | Bump to 960 or 1280 for very wide group shots with small faces |
| `--cpu` | off | Force CPU if CUDA/onnxruntime-gpu is misbehaving |
| `--force` | off | Re-detect everything from scratch |

**Performance:** On a DGX Spark, detection + embedding runs several
photos per second depending on resolution and face count. A 5,000-photo
archive typically finishes in 20–40 minutes.

## Stage B — `faces_cluster.py`

**What it does:** Loads embeddings, runs HDBSCAN (cosine ≈ euclidean
on L2-normalized vectors), groups faces into clusters. Produces:

- `clusters.json` — every cluster's members + centroid.
- `people/montage/index.html` — a standalone static page showing the
  top-N largest clusters as grids of representative face crops. Face
  crops are written as small JPG thumbnails under
  `people/montage/crops/` using OpenCV (raw pixels, so they match the
  stored bbox coordinates regardless of EXIF orientation). Open the
  HTML via any static server — no build step.
- `people/labels.yml` — a starter template with every top cluster
  listed as `cluster_id: skip`, ready for you to edit.

**Knobs:**

| Flag | Default | Effect |
|---|---|---|
| `--min-cluster` | 3 | Smallest cluster size HDBSCAN will form. Lower → more small clusters; higher → more points become noise |
| `--min-samples` | 1 | Density threshold. 1 is loose (fewer noise points, more clusters); higher = stricter |
| `--top` | 60 | How many clusters to render in the montage |
| `--samples` | 16 | Face crops per cluster in the montage |
| `--include-noise` | off | Render the HDBSCAN noise bucket too, so you can spot missed identities |

**Reading the output:** a well-separated archive typically produces
one or two big clusters per major family member (usually split by age
or glasses/hair changes), a long tail of singletons (guests, strangers),
and some false positives (statues, paintings, text that looks face-like).
You only need to label the big ones for the pipeline to be useful.

## Stage C — `faces_label.py`

**Human loop.** You edit `people/labels.yml` with cluster → name
mappings. The file lives under `people/` and is gitignored.

```yaml
# people/labels.yml
cluster_0000: Ma
cluster_0007: Ma            # same person, different decade → merged
cluster_0001: Priya
cluster_0002: Rohan
cluster_0003: reject        # false positives from a statue
cluster_0004: split         # two cousins who look alike; handle later
cluster_0005: skip          # unsure; leave unlabeled
```

**Special values:**

| Value | Meaning |
|---|---|
| `reject` | Cluster is not a real face. Drop all its bbox records. |
| `skip` / absent / commented out | Leave unlabeled. Bboxes kept but `person_name` is null. |
| `split` | Same as `skip`; semantically flags "I need to re-examine this later." |
| Any other string | Treated as a person name. Multiple clusters may map to the same name. |

**Output:**

- `photo_people.json` — `{photo_name: [person_name, ...]}`, one row per
  photo with at least one identified face. Consumed by Stage D.
- `photo_faces.json` — flat list of `{face_id, photo, bbox, person_name,
  det_score, source}`. Includes unlabeled clusters (as `person_name: null`)
  so the viewer can still draw all detected boxes and let a human tag
  unknowns in the UI. Rejected clusters are omitted.

Run `--dry-run` first to see a summary without writing files.

## Stage D — `annotate_local.py`

**What it does:** For each photo, builds a prompt that includes:

- The `family_context.md` prose (same file the cloud path uses).
- **The list of names identified in this specific photo**, from
  `photo_people.json`.
- The photo's EXIF capture date if available (via Pillow).

...and sends it + the image to a local VLM over HTTP (default: Ollama
at `http://localhost:11434`, model `qwen2.5vl:7b`). The VLM returns
JSON in the same schema as `ai_annotate.py` — `scene`, `decade`,
`occasion`, `setting`, `people_description`, `mood`, `text_visible`,
`confidence` — so the existing `POST /api/import` endpoint works
unchanged.

**Why Ollama?** Dead-simple HTTP API, handles model download and GPU
allocation for you, one-line setup. If you want more throughput later,
swap in vLLM or SGLang behind the same `--host`; the client speaks the
standard `/api/generate` shape.

**Model choices on a DGX Spark (128 GB unified memory, ~273 GB/s):**

| Model | Size | Speed | Quality notes |
|---|---|---|---|
| `qwen2.5vl:7b` | ~6 GB | Fastest (1–3 photos/sec) | Strong baseline, solid OCR. Default. |
| `qwen2.5vl:32b` | ~20 GB | Slowest (~1 photo / 3–5 sec) | Noticeably better reasoning and decade estimation |
| `minicpm-v:8b` | ~5 GB | Very fast | Good for scene; weaker OCR |
| `gemma3:12b` | ~8 GB | Medium | Competitive with Qwen-7B |

For a few thousand family photos, any of these finishes overnight.

**Per-photo overhead costs (time, not money):** the VLM is the
bottleneck. Faces + clustering are minutes; VLM is hours.

## Running the full pipeline

```bash
# One-time setup on the Spark
# Always work inside a venv; check for existing tools before installing.
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-local.txt

# Ollama: check first — it may already be installed system-wide.
command -v ollama || curl -fsSL https://ollama.com/install.sh | sh
# Make sure the server is running (systemd unit or `ollama serve &`).
ollama pull qwen2.5vl:7b

# Smoke-test with a small subset first
python scripts/faces_detect.py --limit 20
python scripts/faces_cluster.py --top 10
# open people/montage/index.html, edit people/labels.yml
python scripts/faces_label.py --dry-run
python scripts/faces_label.py
python scripts/annotate_local.py --limit 5

# Full run once you're happy
python scripts/faces_detect.py
python scripts/faces_cluster.py
# (re-edit labels.yml — new clusters may have appeared)
python scripts/faces_label.py
python scripts/annotate_local.py

# Ship it to production
curl -X POST https://family-album-api.sameersegal.workers.dev/api/import \
     -H 'Content-Type: application/json' \
     --data-binary @annotations.json
```

## Integration with the app

The pipeline intentionally keeps its interface with the rest of the
system narrow:

| Artifact | Consumed by |
|---|---|
| `annotations.json` | `POST /api/import` → D1 `photos` table. Identical schema to the cloud path. |
| `photo_people.json` | Also importable (future: extend `/api/import` to bulk-upsert `photo_people` rows). |
| `photo_faces.json` | Future: new endpoint to populate the `photo_faces` D1 table so the viewer can draw bounding boxes. |
| `faces.json` | Stays local. Used for re-clustering and future "find similar faces" features. |

The `photo_faces` table is in the D1 schema already (see
`worker/schema.sql`) so it gets created on your next
`wrangler d1 execute --file=schema.sql`. The API route to populate it
isn't wired up yet — that's a deliberate follow-up so you can see the
JSON shape first.

## Known limitations

- **Face matching does not yet run against a named gallery.** Stage C
  only resolves clusters → names; new photos added later will cluster
  fresh and need re-labeling. A `--match-existing` mode (match new
  faces against labeled-cluster centroids first, cluster only the
  residue) is a reasonable next addition once the base flow is proven.
- **Kids grow up.** ArcFace struggles more across child→adult transitions
  than across middle-age→elderly. Expect to merge 3–4 clusters for
  people photographed across wide age ranges.
- **Twins and strong resemblances.** Siblings who look alike often
  cluster together. Use the `split` label and plan to resolve them
  in the viewer UI (future work).
- **YAML parser is minimal.** `faces_label.py` parses only flat
  `key: value` lines to avoid adding PyYAML. Nested structures are
  not supported and not needed.
- **No montage UI for split/merge.** The current labeling step is
  YAML-in-editor. A click-to-label web UI is plausible future work;
  the current design was chosen to keep scope small.

## File reference

| File | Purpose |
|---|---|
| `scripts/faces_detect.py` | Stage A: InsightFace detection + embedding |
| `scripts/faces_cluster.py` | Stage B: HDBSCAN + montage + labels.yml starter |
| `scripts/faces_label.py` | Stage C: resolve labels.yml into photo_people / photo_faces |
| `scripts/annotate_local.py` | Stage D: local VLM annotation via Ollama |
| `requirements-local.txt` | Python deps (only needed on the Spark) |
| `people/labels.yml` | Human-edited cluster → name map (gitignored) |
| `people/labels.yml.example` | Template with instructions |
| `people/montage/index.html` | Static cluster viewer (gitignored) |
| `faces.json` | Face embeddings + bboxes (gitignored, large) |
| `clusters.json` | HDBSCAN output (gitignored) |
| `photo_people.json` | Per-photo names for Stage D + import (gitignored) |
| `photo_faces.json` | Per-face records for D1 upsert (gitignored) |
| `worker/schema.sql` | D1 schema, includes `photo_faces` table |
