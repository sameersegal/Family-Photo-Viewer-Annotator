#!/usr/bin/env python3
"""
AI Photo Annotator — Uses Claude claude-sonnet-4-6 vision to annotate family photos.

Pass 1 (default):  Annotate each photo individually -> annotations.json
Pass 2 (--cluster): Cluster annotated photos by event/trip -> clusters.json

Prerequisites:
    pip install anthropic

    Export your API key:
        export ANTHROPIC_API_KEY="sk-ant-..."

Usage:
    python ai_annotate.py                   # Pass 1: annotate photos
    python ai_annotate.py --cluster         # Pass 2: cluster annotations
    python ai_annotate.py --help            # Show help

The script reads images from ./images/ and optionally uses family_context.md
for richer, more accurate annotations.
"""

import argparse
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed.")
    print("Install it with:  pip install anthropic")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
IMAGES_DIR = Path("images")
ANNOTATIONS_FILE = Path("annotations.json")
CLUSTERS_FILE = Path("clusters.json")
FAMILY_CONTEXT_FILE = Path("family_context.md")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# Approximate token cost estimates (USD per million tokens, as of 2025)
# Claude claude-sonnet-4-6: $3/M input, $15/M output
INPUT_COST_PER_M = 3.0
OUTPUT_COST_PER_M = 15.0

# Rough estimates for token counting
# A typical photo at medium resolution uses ~1600 tokens via the vision API.
# The text prompt is ~300-600 tokens depending on family context.
ESTIMATED_IMAGE_TOKENS = 1600
ESTIMATED_PROMPT_TOKENS = 500
ESTIMATED_OUTPUT_TOKENS = 400

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

ANNOTATION_SYSTEM_PROMPT = "You are an expert photo analyst helping annotate a family photo archive. You provide accurate, detailed observations and honest confidence assessments."

ANNOTATION_USER_PROMPT_TEMPLATE = """\
You are helping annotate a family photo archive.

{family_context}

Analyze this family photo and return a JSON object with exactly these fields:
- "scene": 2-3 sentence description of what is happening in the photo
- "decade": estimated decade or era (e.g. "1980s", "early 1990s", "2000s")
- "occasion": type of occasion (e.g. "holiday trip", "wedding", "festival celebration", "casual family gathering", "studio portrait", "birthday", "school event", "outdoor excursion")
- "setting": location/setting description (indoor/outdoor, type of place, landmark if recognizable, estimated country/region)
- "people_description": describe the people visible — count, approximate ages, clothing, physical features, grouping
- "mood": mood and atmosphere of the photo (e.g. "joyful and celebratory", "formal and posed", "relaxed and candid")
- "text_visible": any readable text in the photo (signs, banners, labels, watermarks) — use null if none
- "confidence": an object mapping each field name above to "high", "medium", or "low" confidence

Important guidelines:
- Base your analysis only on what is visible in the photo.
- For decade estimation, use clothing styles, photo quality, color tones, and any visible technology or vehicles as clues.
- Be specific about clothing (e.g. "light blue collared shirt" not just "shirt").
- If you cannot determine something, say so honestly and mark confidence as "low".
- Return ONLY valid JSON. No markdown fencing, no commentary outside the JSON object."""


CLUSTER_SYSTEM_PROMPT = "You are an expert photo analyst. You excel at identifying patterns across photo collections — recognizing when photos were taken at the same event, location, or time period based on visual descriptions."

CLUSTER_USER_PROMPT_TEMPLATE = """\
Below are annotations for a collection of family photos. Each entry is keyed by filename.

Your task: group these photos into clusters where photos in the same cluster likely belong to the same event, trip, occasion, or session. Consider:
- Similar settings or locations
- Same occasion type and decade
- Similar people descriptions (same count, similar clothing)
- Logical groupings (e.g. multiple photos from one holiday trip)

{family_context}

Here are the annotations:

{annotations_json}

Return a JSON object with this structure:
{{
  "clusters": [
    {{
      "id": "cluster_1",
      "label": "Short descriptive label for this group",
      "description": "1-2 sentence description of what ties these photos together",
      "photos": ["filename1.jpg", "filename2.jpg"],
      "estimated_date": "approximate date or range",
      "confidence": "high/medium/low"
    }}
  ]
}}

Guidelines:
- A photo can appear in only one cluster.
- Photos that don't clearly belong with others should go in a singleton cluster.
- Prefer fewer, more meaningful clusters over many tiny ones.
- Return ONLY valid JSON. No markdown fencing, no commentary outside the JSON."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_family_context() -> str:
    """Load family_context.md if it exists, otherwise return a placeholder."""
    if FAMILY_CONTEXT_FILE.exists():
        text = FAMILY_CONTEXT_FILE.read_text(encoding="utf-8").strip()
        if text:
            return f"Here is background context about this family:\n\n{text}"
    return "(No family context file provided. Analyze based solely on visual evidence.)"


def get_image_files() -> list[Path]:
    """Return sorted list of image files in the images directory."""
    if not IMAGES_DIR.exists():
        print(f"ERROR: {IMAGES_DIR}/ directory not found.")
        print("Download images first with: python download_images.py <FOLDER_ID>")
        sys.exit(1)

    files = sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )
    return files


def encode_image_base64(image_path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type)."""
    data = image_path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("utf-8")

    # Determine media type
    suffix = image_path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
    }
    media_type = media_type_map.get(suffix)
    if not media_type:
        # Fallback to mimetypes module
        media_type, _ = mimetypes.guess_type(str(image_path))
        if not media_type:
            media_type = "image/jpeg"  # safe default

    return b64, media_type


def load_annotations() -> dict:
    """Load existing annotations from disk, or return empty dict."""
    if ANNOTATIONS_FILE.exists():
        try:
            return json.loads(ANNOTATIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not read {ANNOTATIONS_FILE}: {e}")
            print("Starting with empty annotations.")
    return {}


def save_annotations(annotations: dict) -> None:
    """Save annotations dict to disk."""
    ANNOTATIONS_FILE.write_text(
        json.dumps(annotations, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_clusters(clusters: dict) -> None:
    """Save clusters dict to disk."""
    CLUSTERS_FILE.write_text(
        json.dumps(clusters, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def estimate_cost(num_photos: int) -> dict:
    """Estimate API cost for annotating N photos."""
    input_tokens = num_photos * (ESTIMATED_IMAGE_TOKENS + ESTIMATED_PROMPT_TOKENS)
    output_tokens = num_photos * ESTIMATED_OUTPUT_TOKENS
    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_M
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_M
    total_cost = input_cost + output_cost
    return {
        "num_photos": num_photos,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_usd": round(total_cost, 4),
    }


def parse_json_response(text: str) -> dict | None:
    """
    Attempt to parse JSON from the model response.

    Handles cases where the model wraps JSON in markdown code fences
    despite being told not to.
    """
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (possibly with language tag)
        first_newline = text.index("\n") if "\n" in text else 3
        text = text[first_newline + 1:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


# ---------------------------------------------------------------------------
# Pass 1: Annotate individual photos
# ---------------------------------------------------------------------------

def annotate_photos(dry_run: bool = False) -> None:
    """
    Pass 1: Send each image to Claude for individual annotation.

    Supports resumption by skipping photos already present in annotations.json.
    """
    images = get_image_files()
    if not images:
        print("No images found in ./images/")
        return

    annotations = load_annotations()
    family_context = load_family_context()

    # Determine which photos still need annotation
    remaining = [img for img in images if img.name not in annotations]
    already_done = len(images) - len(remaining)

    print(f"{'=' * 60}")
    print(f"  AI Photo Annotation — Pass 1")
    print(f"{'=' * 60}")
    print(f"  Total images found:     {len(images)}")
    print(f"  Already annotated:      {already_done}")
    print(f"  Remaining to annotate:  {len(remaining)}")
    print()

    if not remaining:
        print("All photos are already annotated. Nothing to do.")
        print(f"Results are in {ANNOTATIONS_FILE}")
        return

    # Cost estimate
    cost = estimate_cost(len(remaining))
    print(f"  Estimated API usage:")
    print(f"    Input tokens:  ~{cost['estimated_input_tokens']:,}")
    print(f"    Output tokens: ~{cost['estimated_output_tokens']:,}")
    print(f"    Estimated cost: ~${cost['estimated_cost_usd']:.4f}")
    print()

    if dry_run:
        print("[DRY RUN] Would annotate the above photos. Exiting.")
        return

    # Build the prompt template (same for all photos)
    prompt_text = ANNOTATION_USER_PROMPT_TEMPLATE.format(
        family_context=family_context
    )

    # Initialize API client
    client = anthropic.Anthropic()

    start_time = time.time()
    success_count = 0
    error_count = 0

    for i, image_path in enumerate(remaining, 1):
        filename = image_path.name
        elapsed = time.time() - start_time
        if i > 1:
            avg_per_photo = elapsed / (i - 1)
            eta = avg_per_photo * (len(remaining) - i + 1)
            eta_str = f" | ETA: {format_duration(eta)}"
        else:
            eta_str = ""

        print(f"[{already_done + i}/{len(images)}] Annotating {filename}...{eta_str}")

        try:
            # Encode the image
            b64_data, media_type = encode_image_base64(image_path)

            # Call Claude vision API
            message = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=ANNOTATION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt_text,
                            },
                        ],
                    }
                ],
            )

            # Extract text response
            response_text = message.content[0].text

            # Parse the JSON
            parsed = parse_json_response(response_text)
            if parsed is None:
                print(f"  WARNING: Failed to parse JSON for {filename}. Saving raw text.")
                annotations[filename] = {
                    "_raw_response": response_text,
                    "_error": "JSON parse failure",
                }
                error_count += 1
            else:
                annotations[filename] = parsed
                success_count += 1

                # Brief summary
                scene_preview = parsed.get("scene", "")[:80]
                decade = parsed.get("decade", "?")
                print(f"  -> {decade} | {scene_preview}...")

            # Save after each photo (enables resumption on interruption)
            save_annotations(annotations)

            # Report token usage from response
            usage = message.usage
            print(
                f"  Tokens: {usage.input_tokens} in / {usage.output_tokens} out"
            )

        except anthropic.APIError as e:
            print(f"  ERROR (API): {e}")
            error_count += 1
            # Continue with next photo
            continue
        except KeyboardInterrupt:
            print("\nInterrupted by user. Progress has been saved.")
            save_annotations(annotations)
            break
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            error_count += 1
            continue

    # Final report
    total_time = time.time() - start_time
    print()
    print(f"{'=' * 60}")
    print(f"  Annotation complete")
    print(f"{'=' * 60}")
    print(f"  Successful: {success_count}")
    print(f"  Errors:     {error_count}")
    print(f"  Total time: {format_duration(total_time)}")
    if success_count > 0:
        print(f"  Avg time per photo: {format_duration(total_time / (success_count + error_count))}")
    print(f"  Results saved to: {ANNOTATIONS_FILE}")
    print()


# ---------------------------------------------------------------------------
# Pass 2: Cluster photos by event/trip
# ---------------------------------------------------------------------------

def cluster_photos() -> None:
    """
    Pass 2: Read all annotations and use Claude to group photos into clusters
    of related images (same event, trip, location, etc.).
    """
    if not ANNOTATIONS_FILE.exists():
        print(f"ERROR: {ANNOTATIONS_FILE} not found.")
        print("Run Pass 1 first:  python ai_annotate.py")
        sys.exit(1)

    annotations = load_annotations()
    if not annotations:
        print("ERROR: annotations.json is empty. Run Pass 1 first.")
        sys.exit(1)

    # Filter out entries with errors
    valid_annotations = {
        k: v for k, v in annotations.items()
        if "_error" not in v
    }

    if not valid_annotations:
        print("ERROR: No valid annotations found. All entries have errors.")
        sys.exit(1)

    family_context = load_family_context()

    print(f"{'=' * 60}")
    print(f"  AI Photo Clustering — Pass 2")
    print(f"{'=' * 60}")
    print(f"  Photos with valid annotations: {len(valid_annotations)}")
    print()

    # Build the annotations summary for the prompt.
    # For very large collections, we send a condensed version to stay within
    # context limits. Each annotation is ~200 tokens, so 200 photos would be
    # ~40k tokens — well within Claude's 200k context.
    annotations_json = json.dumps(valid_annotations, indent=2, ensure_ascii=False)
    annotations_char_count = len(annotations_json)

    # Rough token estimate: ~4 chars per token for English JSON
    estimated_input_tokens = (annotations_char_count // 4) + ESTIMATED_PROMPT_TOKENS
    estimated_output_tokens = max(1000, len(valid_annotations) * 50)
    input_cost = (estimated_input_tokens / 1_000_000) * INPUT_COST_PER_M
    output_cost = (estimated_output_tokens / 1_000_000) * OUTPUT_COST_PER_M
    total_cost = input_cost + output_cost

    print(f"  Annotations payload: ~{annotations_char_count:,} chars")
    print(f"  Estimated tokens: ~{estimated_input_tokens:,} in / ~{estimated_output_tokens:,} out")
    print(f"  Estimated cost: ~${total_cost:.4f}")
    print()

    prompt_text = CLUSTER_USER_PROMPT_TEMPLATE.format(
        family_context=family_context,
        annotations_json=annotations_json,
    )

    client = anthropic.Anthropic()

    print("Sending annotations to Claude for clustering...")
    start_time = time.time()

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=CLUSTER_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": prompt_text,
                }
            ],
        )
    except anthropic.APIError as e:
        print(f"ERROR (API): {e}")
        sys.exit(1)

    elapsed = time.time() - start_time
    response_text = message.content[0].text
    usage = message.usage

    print(f"  Response received in {format_duration(elapsed)}")
    print(f"  Tokens: {usage.input_tokens} in / {usage.output_tokens} out")

    # Parse response
    parsed = parse_json_response(response_text)
    if parsed is None:
        print()
        print("ERROR: Failed to parse clustering response as JSON.")
        print("Raw response saved to clusters_raw.txt for debugging.")
        Path("clusters_raw.txt").write_text(response_text, encoding="utf-8")
        sys.exit(1)

    # Validate structure
    clusters = parsed.get("clusters", [])
    if not clusters:
        print("WARNING: No clusters found in response.")

    save_clusters(parsed)

    # Report
    print()
    print(f"{'=' * 60}")
    print(f"  Clustering complete")
    print(f"{'=' * 60}")
    print(f"  Clusters found: {len(clusters)}")
    print()

    for cluster in clusters:
        cid = cluster.get("id", "?")
        label = cluster.get("label", "Unlabeled")
        photos = cluster.get("photos", [])
        confidence = cluster.get("confidence", "?")
        print(f"  [{cid}] {label} ({len(photos)} photos, confidence: {confidence})")

    print()
    print(f"  Results saved to: {CLUSTERS_FILE}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AI-powered family photo annotator using Claude claude-sonnet-4-6 vision.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python ai_annotate.py                 Annotate all photos (Pass 1)
  python ai_annotate.py --dry-run       Show what would be done without calling API
  python ai_annotate.py --cluster       Cluster photos by event/trip (Pass 2)

The script reads images from ./images/ and saves results to annotations.json.
For better results, create a family_context.md file (see family_context_template.md).
        """,
    )

    parser.add_argument(
        "--cluster",
        action="store_true",
        help="Run Pass 2: cluster annotated photos by event/trip/occasion",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done and estimated cost, without calling the API",
    )

    args = parser.parse_args()

    # Check for API key early
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print()
        print("Set it with:")
        print('  export ANTHROPIC_API_KEY="sk-ant-..."')
        print()
        print("Get your API key from: https://console.anthropic.com/settings/keys")
        sys.exit(1)

    if args.cluster:
        if args.dry_run:
            print("Dry-run is only supported for Pass 1 (annotation).")
            sys.exit(1)
        cluster_photos()
    else:
        annotate_photos(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
