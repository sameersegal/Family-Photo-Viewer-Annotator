CREATE TABLE IF NOT EXISTS photos (
  photo_id     TEXT PRIMARY KEY,
  ai           TEXT DEFAULT NULL,
  confirmed    INTEGER DEFAULT 0,
  confirmed_by TEXT DEFAULT NULL,
  confirmed_at TEXT DEFAULT NULL,
  corrections  TEXT DEFAULT '{}',
  anecdotes    TEXT DEFAULT '[]',
  created_at   TEXT DEFAULT (datetime('now')),
  updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photo_people (
  photo_id    TEXT NOT NULL,
  person_name TEXT NOT NULL,
  PRIMARY KEY (photo_id, person_name),
  FOREIGN KEY (photo_id) REFERENCES photos(photo_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_photo_people_name ON photo_people(person_name);

CREATE TABLE IF NOT EXISTS people (
  name     TEXT PRIMARY KEY,
  added_by TEXT NOT NULL,
  added_at TEXT NOT NULL
);

-- ------------------------------------------------------------
-- AI-detected faces
-- ------------------------------------------------------------
-- Populated by the offline local-AI pipeline (scripts/faces_detect.py →
-- faces_cluster.py → faces_label.py) running on an NVIDIA DGX Spark.
-- Embeddings themselves are NOT stored in D1 — they live in faces.json
-- on the local machine. D1 only gets the resolved bbox + person_name
-- (once a human confirms a cluster label), so the viewer can overlay
-- boxes and the existing photo_people flow stays the canonical source
-- of truth for "who is in this photo".
--
-- person_name is NULL for unlabeled faces (unknown clusters / rejects).
-- source distinguishes 'ai' (auto-assigned from a labeled cluster) from
-- 'human' (someone drew / corrected the box in the UI).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS photo_faces (
  face_id     TEXT PRIMARY KEY,
  photo_id    TEXT NOT NULL,
  bbox_json   TEXT NOT NULL,       -- [x, y, w, h] in pixels
  person_name TEXT,                -- NULL until labeled
  confidence  REAL,                -- detector score, 0..1
  source      TEXT NOT NULL DEFAULT 'ai',  -- 'ai' | 'human'
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (photo_id) REFERENCES photos(photo_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_photo_faces_photo ON photo_faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_faces_person ON photo_faces(person_name);

-- ------------------------------------------------------------
-- User profiles
-- ------------------------------------------------------------
-- Cloudflare Access is the sole authentication gate — only emails on
-- the Access application's policy can log in. This table is NOT an
-- allow-list; it's a profile cache populated on first login, storing
-- the display name the user picks and their role.
--
-- Rows are created automatically the first time a user hits the API.
-- New users default to role='member' with name=NULL (they'll be
-- prompted to set their name). Promote someone to admin manually:
--
--   wrangler d1 execute family-album --remote --command \
--     "UPDATE users SET role='admin' WHERE email='you@example.com';"
--
-- Roles:
--   'admin'  - can delete any anecdote (not just their own)
--   'member' - default; can delete only their own anecdotes
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  email      TEXT PRIMARY KEY,
  name       TEXT,
  role       TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
