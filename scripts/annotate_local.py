#!/usr/bin/env python3
"""
Local VLM annotation — Pass D of the local-AI annotation pipeline.

Calls a local vision-language model (default: Ollama, qwen2.5vl:7b) to
produce the same annotations.json shape as scripts/ai_annotate.py, but
with per-photo identified names injected into the prompt — so scene
descriptions become specific to the family rather than generic.

Inputs:
    images/                    — photos to annotate
    photo_people.json          — from faces_label.py; per-photo names
    family_context.md          — optional free-form background prose
    (EXIF dates on the image files, if present)

Output:
    annotations.json           — same schema as ai_annotate.py produces,
                                 ready for the existing /api/import flow.

Prerequisites:
    # On your DGX Spark:
    curl -fsSL https://ollama.com/install.sh | sh
    ollama pull qwen2.5vl:7b       # or qwen2.5vl:32b for bigger, slower
    ollama serve                   # (usually already running)

    pip install -r requirements-local.txt   # for requests + Pillow (EXIF)

Usage:
    python scripts/annotate_local.py
    python scripts/annotate_local.py --model qwen2.5vl:32b
    python scripts/annotate_local.py --host http://spark.local:11434
    python scripts/annotate_local.py --limit 10        # smoke test
    python scripts/annotate_local.py --force           # re-annotate all
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# Load .env so users with an OLLAMA_HOST or custom model can configure it
# without flags.
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IMAGES_DIR = Path("images")
ANNOTATIONS_FILE = Path("annotations.json")
PHOTO_PEOPLE_FILE = Path("photo_people.json")
FAMILY_CONTEXT_FILE = Path("family_context.md")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("LOCAL_VLM_MODEL", "qwen2.5vl:7b")

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an expert photo analyst helping annotate a family photo archive. "
    "You provide accurate, detailed observations and honest confidence "
    "assessments. You return only valid JSON with no markdown fencing."
)

USER_PROMPT_TEMPLATE = """\
You are helping annotate a family photo archive.

{family_context}

{people_hint}

{exif_hint}

Analyze this family photo and return a JSON object with exactly these fields:
- "scene": 2-3 sentence description of what is happening in the photo. If
  names are provided above, use them naturally (e.g. "Ma and Priya sharing
  tea") rather than generic descriptors.
- "decade": estimated decade or era (e.g. "1980s", "early 1990s", "2000s").
  If an EXIF date hint is provided, use it.
- "occasion": type of occasion (e.g. "holiday trip", "wedding", "festival
  celebration", "casual family gathering", "studio portrait", "birthday",
  "school event", "outdoor excursion").
- "setting": location/setting description — indoor/outdoor, type of place,
  landmark if recognizable, estimated country/region.
- "people_description": describe the people visible — count, approximate
  ages, clothing, physical features, grouping. Refer to named people by
  name; describe unnamed people by visual features.
- "mood": mood and atmosphere of the photo (e.g. "joyful and celebratory",
  "formal and posed", "relaxed and candid").
- "text_visible": any readable text in the photo (signs, banners, labels,
  watermarks). Use null if none.
- "confidence": an object mapping each field name above to "high", "medium",
  or "low" confidence.

Guidelines:
- Base your analysis on what is visible in the photo. Use the provided
  names and dates as confirmed facts when present.
- Be specific about clothing (e.g. "light blue collared shirt" not "shirt").
- If you cannot determine something, say so and mark confidence "low".
- Return ONLY the JSON object. No prose before or after, no markdown fences."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_family_context() -> str:
    if FAMILY_CONTEXT_FILE.exists():
        text = FAMILY_CONTEXT_FILE.read_text(encoding="utf-8").strip()
        if text:
            return f"Background on this family:\n\n{text}"
    return "(No family context file provided.)"


def load_photo_people() -> dict:
    if PHOTO_PEOPLE_FILE.exists():
        try:
            return json.loads(PHOTO_PEOPLE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: could not read {PHOTO_PEOPLE_FILE}: {e}")
    return {}


def load_annotations() -> dict:
    if ANNOTATIONS_FILE.exists():
        try:
            return json.loads(ANNOTATIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_annotations(a: dict) -> None:
    ANNOTATIONS_FILE.write_text(
        json.dumps(a, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_image_files() -> list[Path]:
    if not IMAGES_DIR.exists():
        print(f"ERROR: {IMAGES_DIR}/ not found.")
        sys.exit(1)
    return sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )


def read_exif_datetime(path: Path) -> str | None:
    """Return EXIF DateTimeOriginal (or DateTime) as 'YYYY:MM:DD' if present."""
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # Resolve tag id for DateTimeOriginal, fallback to DateTime.
            tag_ids = {v: k for k, v in ExifTags.TAGS.items()}
            for name in ("DateTimeOriginal", "DateTime"):
                tid = tag_ids.get(name)
                if tid and tid in exif:
                    val = exif[tid]
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="ignore")
                    return str(val).split()[0]
    except Exception:
        return None
    return None


def build_people_hint(names: list[str]) -> str:
    if not names:
        return "People identified in this photo: (none identified by face recognition)."
    return (
        "People identified in this photo by face recognition: "
        + ", ".join(names)
        + ".\nTreat these names as confirmed facts. Use them when describing the scene."
    )


def build_exif_hint(date_str: str | None) -> str:
    if not date_str:
        return "EXIF date: (not available)."
    return f"EXIF capture date: {date_str}. Treat this as a confirmed fact."


def parse_json_response(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        nl = text.index("\n") if "\n" in text else 3
        text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()
    # Some small VLMs prepend a preamble like "Here is the JSON:". Trim to
    # the first '{' if present.
    if not text.startswith("{"):
        brace = text.find("{")
        if brace != -1:
            text = text[brace:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def call_ollama(host: str, model: str, system: str, user: str,
                image_bytes: bytes, timeout: float = 300.0) -> dict:
    """
    Call Ollama /api/generate with an image. Returns the parsed response
    dict (Ollama's structured format, not the VLM's JSON).

    Single-call (non-streaming) for simplicity.
    """
    import requests
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "system": system,
        "prompt": user,
        "images": [b64],
        "stream": False,
        "options": {
            "temperature": 0.2,
            # Ask for enough headroom to not truncate the JSON.
            "num_predict": 1024,
        },
    }
    r = requests.post(f"{host}/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


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
        description="Annotate photos with a local VLM (Ollama).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Ollama server URL (default: {DEFAULT_HOST}). "
                             f"Also settable via OLLAMA_HOST env var.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model tag to call (default: {DEFAULT_MODEL}). "
                             f"Try qwen2.5vl:32b or minicpm-v:8b.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Annotate only the first N remaining photos.")
    parser.add_argument("--force", action="store_true",
                        help="Re-annotate even photos already in annotations.json.")
    parser.add_argument("--no-exif", action="store_true",
                        help="Skip EXIF date extraction.")
    args = parser.parse_args()

    # Lazy deps
    try:
        import requests  # noqa: F401
    except ImportError:
        print("ERROR: requests not installed.  pip install -r requirements-local.txt")
        sys.exit(1)

    images = get_image_files()
    annotations = {} if args.force else load_annotations()
    photo_people = load_photo_people()
    family_context = load_family_context()

    remaining = [p for p in images if p.name not in annotations]
    if args.limit and args.limit < len(remaining):
        remaining = remaining[: args.limit]

    print(f"{'=' * 60}")
    print(f"  Local VLM Annotation")
    print(f"{'=' * 60}")
    print(f"  Host:            {args.host}")
    print(f"  Model:           {args.model}")
    print(f"  Total images:    {len(images)}")
    print(f"  Already done:    {len(images) - len([p for p in images if p.name not in annotations])}")
    print(f"  To annotate:     {len(remaining)}")
    print(f"  Photos with identified people: "
          f"{sum(1 for p in remaining if photo_people.get(p.name))}")
    print()

    if not remaining:
        print("Nothing to do.")
        return

    start = time.time()
    ok = 0
    errs = 0
    save_every = 10

    for i, img_path in enumerate(remaining, 1):
        filename = img_path.name
        elapsed = time.time() - start
        if i > 1:
            avg = elapsed / (i - 1)
            eta = avg * (len(remaining) - i + 1)
            eta_str = f" | ETA: {format_duration(eta)}"
        else:
            eta_str = ""

        names = photo_people.get(filename, [])
        exif_date = None if args.no_exif else read_exif_datetime(img_path)

        user_prompt = USER_PROMPT_TEMPLATE.format(
            family_context=family_context,
            people_hint=build_people_hint(names),
            exif_hint=build_exif_hint(exif_date),
        )

        label = ", ".join(names) if names else "no-id"
        print(f"[{i}/{len(remaining)}] {filename} ({label}){eta_str}")

        try:
            image_bytes = img_path.read_bytes()
            resp = call_ollama(
                args.host, args.model, SYSTEM_PROMPT, user_prompt, image_bytes,
            )
            response_text = resp.get("response", "")
            parsed = parse_json_response(response_text)
            if parsed is None:
                print(f"  WARNING: JSON parse failed. Saving raw.")
                annotations[filename] = {
                    "_raw_response": response_text,
                    "_error": "JSON parse failure",
                    "_model": args.model,
                }
                errs += 1
            else:
                # Stamp provenance so we can tell local- vs cloud-annotated
                # photos apart.
                parsed.setdefault("_model", args.model)
                if names:
                    parsed.setdefault("_identified_people", names)
                annotations[filename] = parsed
                ok += 1
                scene = parsed.get("scene", "")[:80]
                decade = parsed.get("decade", "?")
                print(f"  -> {decade} | {scene}...")

            # Show token-ish metrics if Ollama returned them
            if "eval_count" in resp:
                print(
                    f"  Tokens: {resp.get('prompt_eval_count', '?')} in / "
                    f"{resp.get('eval_count', '?')} out  "
                    f"({resp.get('eval_duration', 0) / 1e9:.1f}s eval)"
                )

            if i % save_every == 0:
                save_annotations(annotations)

        except KeyboardInterrupt:
            print("\nInterrupted. Saving progress...")
            save_annotations(annotations)
            sys.exit(130)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            errs += 1

    save_annotations(annotations)

    total = time.time() - start
    print()
    print(f"{'=' * 60}")
    print(f"  Done")
    print(f"{'=' * 60}")
    print(f"  Successful:    {ok}")
    print(f"  Errors:        {errs}")
    print(f"  Elapsed:       {format_duration(total)}")
    if ok > 0:
        print(f"  Avg per photo: {format_duration(total / (ok + errs))}")
    print(f"  Output:        {ANNOTATIONS_FILE}")
    print()
    print("Import into D1:")
    print("  curl -X POST https://.../api/import \\")
    print("       -H 'Content-Type: application/json' \\")
    print(f"       --data-binary @{ANNOTATIONS_FILE}")


if __name__ == "__main__":
    main()
