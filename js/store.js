/**
 * Family Photo Album - Data Persistence Layer
 *
 * Uses Cloudflare Worker API when configured, falls back to localStorage.
 * Provides a unified interface for annotations, people tags, and anecdotes.
 */
import { CONFIG } from './config.js';

const STORAGE_KEY = 'familyAlbumData';
const USER_KEY = 'familyAlbumUser';
const PEOPLE_KEY = 'familyAlbumPeople';

let useApi = false;
let API_BASE = '';

// ============================================================
// INITIALIZATION
// ============================================================
export async function init() {
  const workerUrl = CONFIG.api?.workerUrl;
  if (workerUrl) {
    try {
      const resp = await fetch(`${workerUrl}/api/people`, { method: 'GET' });
      if (resp.ok) {
        useApi = true;
        API_BASE = workerUrl;
        console.log('[store] Worker API connected');
      } else {
        console.warn('[store] Worker API returned', resp.status, '— using localStorage');
      }
    } catch (e) {
      console.warn('[store] Worker API unreachable, using localStorage:', e.message);
    }
  } else {
    console.log('[store] No workerUrl configured — using localStorage');
  }
}

// ============================================================
// API FETCH HELPER
// ============================================================
async function apiFetch(path, options = {}) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    throw new Error(`API error ${resp.status}: ${await resp.text()}`);
  }
  return resp.json();
}

// ============================================================
// LOCAL STORAGE HELPERS
// ============================================================
function getLocalData() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function setLocalData(data) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function createEmptyAnnotation() {
  return {
    ai: null,
    confirmed: false,
    confirmedBy: null,
    corrections: {},
    people: [],
    anecdotes: [],
  };
}

// ============================================================
// PHOTO ANNOTATION CRUD
// ============================================================
export async function getAnnotation(photoId) {
  if (useApi) {
    return apiFetch(`/api/photos/${encodeURIComponent(photoId)}`);
  }
  const data = getLocalData();
  return data[photoId] || createEmptyAnnotation();
}

export async function saveAnnotation(photoId, updates) {
  if (useApi) {
    await apiFetch(`/api/photos/${encodeURIComponent(photoId)}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
    return;
  }
  const data = getLocalData();
  data[photoId] = { ...(data[photoId] || createEmptyAnnotation()), ...updates };
  setLocalData(data);
}

export async function getAllAnnotations() {
  if (useApi) {
    return apiFetch('/api/photos');
  }
  return getLocalData();
}

// ============================================================
// CONFIRM / CORRECT AI ANNOTATION
// ============================================================
export async function confirmAnnotation(photoId) {
  if (useApi) {
    await apiFetch(`/api/photos/${encodeURIComponent(photoId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ confirmedBy: getCurrentUser() }),
    });
    return;
  }
  await saveAnnotation(photoId, {
    confirmed: true,
    confirmedBy: getCurrentUser(),
    confirmedAt: new Date().toISOString(),
  });
}

export async function saveCorrection(photoId, corrections) {
  if (useApi) {
    await apiFetch(`/api/photos/${encodeURIComponent(photoId)}/corrections`, {
      method: 'POST',
      body: JSON.stringify({ corrections, confirmedBy: getCurrentUser() }),
    });
    return;
  }
  const annotation = await getAnnotation(photoId);
  const merged = { ...(annotation.corrections || {}), ...corrections };
  await saveAnnotation(photoId, {
    corrections: merged,
    confirmed: true,
    confirmedBy: getCurrentUser(),
    confirmedAt: new Date().toISOString(),
  });
}

// ============================================================
// ANECDOTES
// ============================================================
export async function addAnecdote(photoId, author, text) {
  if (useApi) {
    return apiFetch(`/api/photos/${encodeURIComponent(photoId)}/anecdotes`, {
      method: 'POST',
      body: JSON.stringify({ author, text }),
    });
  }
  const annotation = await getAnnotation(photoId);
  const anecdotes = annotation.anecdotes || [];
  anecdotes.push({ author, text, timestamp: new Date().toISOString() });
  await saveAnnotation(photoId, { anecdotes });
  return anecdotes;
}

export async function deleteAnecdote(photoId, index) {
  if (useApi) {
    return apiFetch(`/api/photos/${encodeURIComponent(photoId)}/anecdotes/${index}`, {
      method: 'DELETE',
    });
  }
  const annotation = await getAnnotation(photoId);
  const anecdotes = annotation.anecdotes || [];
  anecdotes.splice(index, 1);
  await saveAnnotation(photoId, { anecdotes });
  return anecdotes;
}

// ============================================================
// PEOPLE TAGGING
// ============================================================
export async function tagPerson(photoId, name) {
  const normalized = name.trim();
  if (!normalized) return;
  if (useApi) {
    return apiFetch(`/api/photos/${encodeURIComponent(photoId)}/people`, {
      method: 'POST',
      body: JSON.stringify({ name: normalized, addedBy: getCurrentUser() }),
    });
  }
  const annotation = await getAnnotation(photoId);
  const people = annotation.people || [];
  if (!people.includes(normalized)) {
    people.push(normalized);
    await saveAnnotation(photoId, { people });
    await addToPeopleDirectory(normalized);
  }
  return people;
}

export async function untagPerson(photoId, name) {
  if (useApi) {
    return apiFetch(
      `/api/photos/${encodeURIComponent(photoId)}/people/${encodeURIComponent(name)}`,
      { method: 'DELETE' }
    );
  }
  const annotation = await getAnnotation(photoId);
  const people = (annotation.people || []).filter(p => p !== name);
  await saveAnnotation(photoId, { people });
  return people;
}

// ============================================================
// PEOPLE DIRECTORY
// ============================================================
async function addToPeopleDirectory(name) {
  // Only used in localStorage path; API handles this server-side
  const people = JSON.parse(localStorage.getItem(PEOPLE_KEY) || '[]');
  if (!people.includes(name)) {
    people.push(name);
    localStorage.setItem(PEOPLE_KEY, JSON.stringify(people));
  }
}

export async function getAllPeople() {
  if (useApi) {
    return apiFetch('/api/people');
  }
  return JSON.parse(localStorage.getItem(PEOPLE_KEY) || '[]').sort();
}

// ============================================================
// AI ANNOTATIONS IMPORT
// ============================================================
export async function importAIAnnotations(aiData) {
  if (useApi) {
    const result = await apiFetch('/api/import', {
      method: 'POST',
      body: JSON.stringify(aiData),
    });
    return result.imported;
  }
  let imported = 0;
  for (const [photoId, ai] of Object.entries(aiData)) {
    const existing = await getAnnotation(photoId);
    if (!existing.ai) {
      await saveAnnotation(photoId, { ai, confirmed: false });
      imported++;
    }
  }
  return imported;
}

// ============================================================
// USER MANAGEMENT
// ============================================================
export function getCurrentUser() {
  return localStorage.getItem(USER_KEY) || '';
}

export function setCurrentUser(name) {
  localStorage.setItem(USER_KEY, name.trim());
}
