# Family Photo Viewer & Annotator

A web application for browsing, annotating, and sharing stories about family photos. Features AI-powered photo descriptions using Claude's vision API, collaborative people tagging, and a full-screen slideshow mode.

## Features

- **Gallery View** -- Browse photos in a responsive grid with full-text search and people filter chips
- **Detail View** -- View a photo with its AI-generated description, tag people, and add stories/memories
- **Slideshow** -- Full-screen auto-advancing slideshow with fade transitions and annotation overlays
- **AI Annotations** -- Two-pass pipeline: individual photo descriptions, then event/trip clustering
- **Collaborative** -- Multiple family members can tag people, correct AI descriptions, and share anecdotes
- **Flexible Storage** -- Works with local files or Cloudflare R2; Firebase Firestore or localStorage for data

## Project Structure

```
.
├── index.html                  # App entry point
├── js/                         # Frontend JavaScript (ES modules)
│   ├── app.js                  # Main SPA logic (routing, views, events)
│   ├── store.js                # Data persistence (Firestore / localStorage)
│   └── config.js               # App configuration
├── css/
│   └── styles.css              # Styling (warm beige/gold theme)
├── assets/
│   └── frame.png               # Slideshow frame overlay
├── scripts/                    # Python utility scripts
│   ├── serve.py                # Local dev server
│   ├── build.py                # Generate manifest.json (+ optional thumbnails)
│   ├── ai_annotate.py          # AI photo annotation via Claude vision API
│   ├── download_images.py      # Download photos from Google Drive
│   └── upload_to_r2.py         # Upload photos & thumbnails to Cloudflare R2
├── family_context_template.md  # Template for family background info
├── requirements.txt            # Python dependencies
└── .gitignore
```

## Quick Start

### 1. Get your photos

Place your photos in an `images/` directory at the project root, or download them from Google Drive:

```bash
pip install -r requirements.txt
python scripts/download_images.py <GOOGLE_DRIVE_FOLDER_ID>
```

### 2. Build the manifest

```bash
python scripts/build.py
# Or with local thumbnails for faster gallery loading:
python scripts/build.py --thumbs
```

### 3. Start the dev server

```bash
python scripts/serve.py
# Open http://localhost:8765
```

### 4. (Optional) Generate AI annotations

Requires an [Anthropic API key](https://console.anthropic.com/settings/keys):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."

# Pass 1: Annotate each photo individually
python scripts/ai_annotate.py

# Pass 2: Cluster photos by event/trip
python scripts/ai_annotate.py --cluster
```

For better results, copy `family_context_template.md` to `family_context.md` and fill in details about your family members, known trips, and cultural context.

## Configuration

Edit `js/config.js` to configure:

| Setting | Description |
|---|---|
| `imageSource` | `'local'` for `./images/` or `'r2'` for Cloudflare R2 |
| `r2.publicUrl` | Your R2 public URL (when using R2) |
| `firebase.*` | Firebase credentials for shared annotations (leave `apiKey` empty for localStorage) |
| `slideshow.autoAdvanceMs` | Slideshow auto-advance interval (default: 6000ms) |

## Deployment (Production)

For a production setup with shared access across devices:

### Image hosting with Cloudflare R2

```bash
export R2_ACCOUNT_ID="your-account-id"
export R2_ACCESS_KEY_ID="your-access-key-id"
export R2_SECRET_ACCESS_KEY="your-secret-access-key"
export R2_BUCKET_NAME="family-photos"

python scripts/upload_to_r2.py
```

Then set `imageSource: 'r2'` and the `r2.publicUrl` in `js/config.js`.

### Shared annotations with Firebase

1. Create a Firebase project and enable Firestore
2. Add your Firebase config to `js/config.js`
3. Deploy the HTML/JS/CSS to any static host (Netlify, Vercel, GitHub Pages, etc.)

## Keyboard Shortcuts

### Slideshow
| Key | Action |
|---|---|
| Right Arrow / Space | Next photo |
| Left Arrow | Previous photo |
| P | Pause / resume |
| Escape | Exit slideshow |

### Detail View
| Key | Action |
|---|---|
| Right Arrow | Next photo |
| Left Arrow | Previous photo |
| Escape | Back to gallery |

## Tech Stack

- **Frontend**: Vanilla JavaScript (ES modules), HTML5, CSS3 -- no build step required
- **AI**: Claude Sonnet (via Anthropic API) for photo descriptions and clustering
- **Storage**: Firebase Firestore (shared) or localStorage (single device)
- **Images**: Local files or Cloudflare R2
- **Scripts**: Python 3 for image processing, uploads, and AI annotation

## Requirements

- A modern web browser (ES module support)
- Python 3.10+ (for the utility scripts)
- See `requirements.txt` for Python package dependencies
