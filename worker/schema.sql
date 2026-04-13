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
