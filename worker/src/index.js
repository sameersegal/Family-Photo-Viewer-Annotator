/**
 * Family Album API — Cloudflare Worker + D1
 *
 * REST API for shared photo annotations, people tags, and anecdotes.
 *
 * Every request is authenticated via Cloudflare Access (Email OTP). See
 * ./auth.js for details. The caller's identity (email + name) comes from
 * the verified Access JWT — clients cannot spoof it.
 */
import { authenticate, setUserName, AuthError } from './auth.js';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const method = request.method;

    // CORS preflight — no auth required (browsers never send credentials on OPTIONS)
    if (method === 'OPTIONS') {
      return corsResponse(new Response(null, { status: 204 }), request, env);
    }

    let user;
    try {
      user = await authenticate(request, env);
    } catch (err) {
      if (err instanceof AuthError) {
        return corsResponse(json({ error: err.message }, err.status), request, env);
      }
      return corsResponse(json({ error: 'Authentication failed' }, 500), request, env);
    }

    try {
      const response = await handleRequest(method, url.pathname, request, env, user);
      return corsResponse(response, request, env);
    } catch (err) {
      if (err instanceof AuthError) {
        return corsResponse(json({ error: err.message }, err.status), request, env);
      }
      return corsResponse(json({ error: err.message }, 500), request, env);
    }
  },
};

// ============================================================
// ROUTER
// ============================================================

async function handleRequest(method, path, request, env, user) {
  const db = env.DB;

  // GET /api/me — who am I?
  if (method === 'GET' && path === '/api/me') {
    return json(user);
  }

  // PATCH /api/me — update my display name
  if (method === 'PATCH' && path === '/api/me') {
    const body = await request.json();
    const updated = await setUserName(env, user.email, body.name);
    return json(updated);
  }

  // GET /api/photos
  if (method === 'GET' && path === '/api/photos') {
    return getAllPhotos(db);
  }

  // GET /api/people
  if (method === 'GET' && path === '/api/people') {
    return getAllPeople(db);
  }

  // POST /api/import
  if (method === 'POST' && path === '/api/import') {
    return importAIAnnotations(db, await request.json());
  }

  // /api/photos/:id routes
  const photoMatch = path.match(/^\/api\/photos\/([^/]+)$/);
  if (photoMatch) {
    const id = decodeURIComponent(photoMatch[1]);
    if (method === 'GET') return getPhoto(db, id);
    if (method === 'PATCH') return patchPhoto(db, id, await request.json());
  }

  // /api/photos/:id/confirm
  const confirmMatch = path.match(/^\/api\/photos\/([^/]+)\/confirm$/);
  if (confirmMatch && method === 'POST') {
    return confirmPhoto(db, decodeURIComponent(confirmMatch[1]), user);
  }

  // /api/photos/:id/corrections
  const correctionsMatch = path.match(/^\/api\/photos\/([^/]+)\/corrections$/);
  if (correctionsMatch && method === 'POST') {
    return saveCorrections(db, decodeURIComponent(correctionsMatch[1]), await request.json(), user);
  }

  // /api/photos/:id/anecdotes
  const anecdotesMatch = path.match(/^\/api\/photos\/([^/]+)\/anecdotes$/);
  if (anecdotesMatch && method === 'POST') {
    return addAnecdote(db, decodeURIComponent(anecdotesMatch[1]), await request.json(), user);
  }

  // DELETE /api/photos/:id/anecdotes/:idx
  const anecdoteDeleteMatch = path.match(/^\/api\/photos\/([^/]+)\/anecdotes\/(\d+)$/);
  if (anecdoteDeleteMatch && method === 'DELETE') {
    return deleteAnecdote(
      db,
      decodeURIComponent(anecdoteDeleteMatch[1]),
      parseInt(anecdoteDeleteMatch[2], 10),
      user
    );
  }

  // /api/photos/:id/people
  const peopleMatch = path.match(/^\/api\/photos\/([^/]+)\/people$/);
  if (peopleMatch && method === 'POST') {
    return tagPerson(db, decodeURIComponent(peopleMatch[1]), await request.json(), user);
  }

  // DELETE /api/photos/:id/people/:name
  const personDeleteMatch = path.match(/^\/api\/photos\/([^/]+)\/people\/([^/]+)$/);
  if (personDeleteMatch && method === 'DELETE') {
    return untagPerson(
      db,
      decodeURIComponent(personDeleteMatch[1]),
      decodeURIComponent(personDeleteMatch[2])
    );
  }

  return json({ error: 'Not found' }, 404);
}

// ============================================================
// ROUTE HANDLERS
// ============================================================

async function getAllPhotos(db) {
  const photos = await db.prepare('SELECT * FROM photos').all();
  const people = await db.prepare('SELECT * FROM photo_people').all();

  const peopleMap = {};
  for (const row of people.results) {
    if (!peopleMap[row.photo_id]) peopleMap[row.photo_id] = [];
    peopleMap[row.photo_id].push(row.person_name);
  }

  const result = {};
  for (const row of photos.results) {
    result[row.photo_id] = rowToAnnotation(row, peopleMap[row.photo_id] || []);
  }
  return json(result);
}

async function getPhoto(db, photoId) {
  const row = await db.prepare('SELECT * FROM photos WHERE photo_id = ?').bind(photoId).first();
  if (!row) {
    return json({ ai: null, confirmed: false, confirmedBy: null, corrections: {}, people: [], anecdotes: [] });
  }
  const people = await db
    .prepare('SELECT person_name FROM photo_people WHERE photo_id = ?')
    .bind(photoId)
    .all();
  const names = people.results.map(r => r.person_name);
  return json(rowToAnnotation(row, names));
}

async function patchPhoto(db, photoId, updates) {
  await ensurePhoto(db, photoId);

  const sets = [];
  const binds = [];

  if (updates.ai !== undefined) {
    sets.push('ai = ?');
    binds.push(JSON.stringify(updates.ai));
  }
  if (updates.confirmed !== undefined) {
    sets.push('confirmed = ?');
    binds.push(updates.confirmed ? 1 : 0);
  }
  if (updates.confirmedBy !== undefined) {
    sets.push('confirmed_by = ?');
    binds.push(updates.confirmedBy);
  }
  if (updates.confirmedAt !== undefined) {
    sets.push('confirmed_at = ?');
    binds.push(updates.confirmedAt);
  }
  if (updates.corrections !== undefined) {
    sets.push('corrections = ?');
    binds.push(JSON.stringify(updates.corrections));
  }
  if (updates.anecdotes !== undefined) {
    sets.push('anecdotes = ?');
    binds.push(JSON.stringify(updates.anecdotes));
  }

  if (sets.length > 0) {
    sets.push("updated_at = datetime('now')");
    const sql = `UPDATE photos SET ${sets.join(', ')} WHERE photo_id = ?`;
    binds.push(photoId);
    await db.prepare(sql).bind(...binds).run();
  }

  // Handle people array if provided
  if (updates.people !== undefined) {
    await db.prepare('DELETE FROM photo_people WHERE photo_id = ?').bind(photoId).run();
    for (const name of updates.people) {
      await db
        .prepare('INSERT OR IGNORE INTO photo_people (photo_id, person_name) VALUES (?, ?)')
        .bind(photoId, name)
        .run();
    }
  }

  return getPhoto(db, photoId);
}

async function confirmPhoto(db, photoId, user) {
  await ensurePhoto(db, photoId);
  const now = new Date().toISOString();
  await db
    .prepare(
      "UPDATE photos SET confirmed = 1, confirmed_by = ?, confirmed_at = ?, updated_at = datetime('now') WHERE photo_id = ?"
    )
    .bind((user.name || user.email), now, photoId)
    .run();
  return getPhoto(db, photoId);
}

async function saveCorrections(db, photoId, body, user) {
  await ensurePhoto(db, photoId);

  // Merge corrections with existing
  const row = await db.prepare('SELECT corrections FROM photos WHERE photo_id = ?').bind(photoId).first();
  const existing = JSON.parse(row.corrections || '{}');
  const merged = { ...existing, ...body.corrections };
  const now = new Date().toISOString();

  await db
    .prepare(
      "UPDATE photos SET corrections = ?, confirmed = 1, confirmed_by = ?, confirmed_at = ?, updated_at = datetime('now') WHERE photo_id = ?"
    )
    .bind(JSON.stringify(merged), (user.name || user.email), now, photoId)
    .run();

  return getPhoto(db, photoId);
}

async function addAnecdote(db, photoId, body, user) {
  await ensurePhoto(db, photoId);

  const row = await db.prepare('SELECT anecdotes FROM photos WHERE photo_id = ?').bind(photoId).first();
  const anecdotes = JSON.parse(row.anecdotes || '[]');
  anecdotes.push({
    author: (user.name || user.email),
    authorEmail: user.email,
    text: body.text,
    timestamp: new Date().toISOString(),
  });

  await db
    .prepare("UPDATE photos SET anecdotes = ?, updated_at = datetime('now') WHERE photo_id = ?")
    .bind(JSON.stringify(anecdotes), photoId)
    .run();

  return json(anecdotes);
}

async function deleteAnecdote(db, photoId, index, user) {
  const row = await db.prepare('SELECT anecdotes FROM photos WHERE photo_id = ?').bind(photoId).first();
  if (!row) return json([]);

  const anecdotes = JSON.parse(row.anecdotes || '[]');
  if (index >= 0 && index < anecdotes.length) {
    const target = anecdotes[index];
    // Only the author (by email) or an admin may delete
    const isAuthor = target.authorEmail && target.authorEmail === user.email;
    const isAdmin = user.role === 'admin';
    if (!isAuthor && !isAdmin) {
      return json({ error: 'You can only delete your own stories.' }, 403);
    }
    anecdotes.splice(index, 1);
    await db
      .prepare("UPDATE photos SET anecdotes = ?, updated_at = datetime('now') WHERE photo_id = ?")
      .bind(JSON.stringify(anecdotes), photoId)
      .run();
  }

  return json(anecdotes);
}

async function tagPerson(db, photoId, body, user) {
  const name = (body.name || '').trim();
  if (!name) return json({ error: 'name required' }, 400);

  await ensurePhoto(db, photoId);

  await db
    .prepare('INSERT OR IGNORE INTO photo_people (photo_id, person_name) VALUES (?, ?)')
    .bind(photoId, name)
    .run();

  // Add to people directory
  await db
    .prepare('INSERT OR IGNORE INTO people (name, added_by, added_at) VALUES (?, ?, ?)')
    .bind(name, (user.name || user.email), new Date().toISOString())
    .run();

  const people = await db
    .prepare('SELECT person_name FROM photo_people WHERE photo_id = ?')
    .bind(photoId)
    .all();
  return json(people.results.map(r => r.person_name));
}

async function untagPerson(db, photoId, name) {
  await db
    .prepare('DELETE FROM photo_people WHERE photo_id = ? AND person_name = ?')
    .bind(photoId, name)
    .run();

  const people = await db
    .prepare('SELECT person_name FROM photo_people WHERE photo_id = ?')
    .bind(photoId)
    .all();
  return json(people.results.map(r => r.person_name));
}

async function getAllPeople(db) {
  const rows = await db.prepare('SELECT name FROM people ORDER BY name').all();
  return json(rows.results.map(r => r.name));
}

async function importAIAnnotations(db, body) {
  // Accept either:
  //   { photoId: ai, ... }                                 (legacy flat shape)
  //   { data: {...}, photo_people?: {photoId: [names]} }   (extended shape)
  const data = body.data || (body.photo_people ? {} : body);
  const photoPeople = body.photo_people || {};
  let imported = 0;
  let peopleLinked = 0;

  for (const [photoId, ai] of Object.entries(data)) {
    const existing = await db
      .prepare('SELECT ai FROM photos WHERE photo_id = ?')
      .bind(photoId)
      .first();

    if (!existing) {
      await db
        .prepare('INSERT INTO photos (photo_id, ai, confirmed) VALUES (?, ?, 0)')
        .bind(photoId, JSON.stringify(ai))
        .run();
      imported++;
    } else if (!existing.ai) {
      await db
        .prepare("UPDATE photos SET ai = ?, updated_at = datetime('now') WHERE photo_id = ?")
        .bind(JSON.stringify(ai), photoId)
        .run();
      imported++;
    }
  }

  // Cluster-resolved people for each photo. Idempotent: INSERT OR IGNORE on
  // both tables, so re-running the import never creates duplicates. Any
  // names the user removes via the UI will stay removed until the next
  // import brings them back — acceptable because the cluster labels are
  // considered ground truth from the offline pipeline.
  const nowIso = new Date().toISOString();
  for (const [photoId, names] of Object.entries(photoPeople)) {
    if (!Array.isArray(names) || names.length === 0) continue;
    // Make sure the photos row exists even if there's no ai blob for it.
    await db
      .prepare('INSERT OR IGNORE INTO photos (photo_id) VALUES (?)')
      .bind(photoId)
      .run();
    for (const name of names) {
      await db
        .prepare('INSERT OR IGNORE INTO people (name, added_by, added_at) VALUES (?, ?, ?)')
        .bind(name, 'ai-import', nowIso)
        .run();
      const res = await db
        .prepare('INSERT OR IGNORE INTO photo_people (photo_id, person_name) VALUES (?, ?)')
        .bind(photoId, name)
        .run();
      if (res.meta && res.meta.changes) peopleLinked += res.meta.changes;
    }
  }

  return json({ imported, peopleLinked });
}

// ============================================================
// HELPERS
// ============================================================

async function ensurePhoto(db, photoId) {
  await db
    .prepare('INSERT OR IGNORE INTO photos (photo_id) VALUES (?)')
    .bind(photoId)
    .run();
}

function rowToAnnotation(row, people) {
  return {
    ai: row.ai ? JSON.parse(row.ai) : null,
    confirmed: row.confirmed === 1,
    confirmedBy: row.confirmed_by || null,
    confirmedAt: row.confirmed_at || null,
    corrections: JSON.parse(row.corrections || '{}'),
    people: people,
    anecdotes: JSON.parse(row.anecdotes || '[]'),
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * CORS with credentials. We must echo a specific Origin (not `*`) because
 * the browser refuses credentialed responses with wildcard origins.
 * The ALLOWED_ORIGINS env var is a comma-separated list.
 */
function corsResponse(response, request, env) {
  const origin = request.headers.get('Origin');
  const allowed = (env.ALLOWED_ORIGINS || '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);

  if (origin && allowed.includes(origin)) {
    response.headers.set('Access-Control-Allow-Origin', origin);
    response.headers.set('Access-Control-Allow-Credentials', 'true');
    response.headers.set('Vary', 'Origin');
  }
  response.headers.set('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS');
  response.headers.set(
    'Access-Control-Allow-Headers',
    'Content-Type, Cf-Access-Jwt-Assertion'
  );
  return response;
}
