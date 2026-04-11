/**
 * Family Photo Album - Data Persistence Layer
 *
 * Uses Firebase Firestore when configured, falls back to localStorage.
 * Provides a unified interface for annotations, people tags, and anecdotes.
 */
import { CONFIG } from './config.js';

const STORAGE_KEY = 'familyAlbumData';
const USER_KEY = 'familyAlbumUser';
const PEOPLE_KEY = 'familyAlbumPeople';

let firestore = null;
let fs = null; // cached Firestore module functions
let useFirebase = false;

// ============================================================
// INITIALIZATION
// ============================================================
export async function init() {
  const fc = CONFIG.firebase;
  if (fc.apiKey) {
    try {
      const appMod = await import('https://www.gstatic.com/firebasejs/10.14.0/firebase-app.js');
      const fsMod = await import('https://www.gstatic.com/firebasejs/10.14.0/firebase-firestore.js');
      const app = appMod.initializeApp(fc);
      firestore = fsMod.getFirestore(app);
      fs = fsMod;
      useFirebase = true;
      console.log('[store] Firebase Firestore connected');
    } catch (e) {
      console.warn('[store] Firebase init failed, using localStorage:', e);
    }
  } else {
    console.log('[store] No Firebase config — using localStorage');
  }
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
  if (useFirebase) {
    const snap = await fs.getDoc(fs.doc(firestore, 'photos', photoId));
    return snap.exists() ? snap.data() : createEmptyAnnotation();
  }
  const data = getLocalData();
  return data[photoId] || createEmptyAnnotation();
}

export async function saveAnnotation(photoId, updates) {
  if (useFirebase) {
    await fs.setDoc(fs.doc(firestore, 'photos', photoId), updates, { merge: true });
    return;
  }
  const data = getLocalData();
  data[photoId] = { ...(data[photoId] || createEmptyAnnotation()), ...updates };
  setLocalData(data);
}

export async function getAllAnnotations() {
  if (useFirebase) {
    const snap = await fs.getDocs(fs.collection(firestore, 'photos'));
    const result = {};
    snap.docs.forEach(d => { result[d.id] = d.data(); });
    return result;
  }
  return getLocalData();
}

// ============================================================
// CONFIRM / CORRECT AI ANNOTATION
// ============================================================
export async function confirmAnnotation(photoId) {
  await saveAnnotation(photoId, {
    confirmed: true,
    confirmedBy: getCurrentUser(),
    confirmedAt: new Date().toISOString(),
  });
}

export async function saveCorrection(photoId, corrections) {
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
  const annotation = await getAnnotation(photoId);
  const anecdotes = annotation.anecdotes || [];
  anecdotes.push({ author, text, timestamp: new Date().toISOString() });
  await saveAnnotation(photoId, { anecdotes });
  return anecdotes;
}

export async function deleteAnecdote(photoId, index) {
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
  const annotation = await getAnnotation(photoId);
  const people = (annotation.people || []).filter(p => p !== name);
  await saveAnnotation(photoId, { people });
  return people;
}

// ============================================================
// PEOPLE DIRECTORY
// ============================================================
async function addToPeopleDirectory(name) {
  if (useFirebase) {
    await fs.setDoc(
      fs.doc(firestore, 'people', name),
      { name, addedBy: getCurrentUser(), addedAt: new Date().toISOString() },
      { merge: true }
    );
    return;
  }
  const people = JSON.parse(localStorage.getItem(PEOPLE_KEY) || '[]');
  if (!people.includes(name)) {
    people.push(name);
    localStorage.setItem(PEOPLE_KEY, JSON.stringify(people));
  }
}

export async function getAllPeople() {
  if (useFirebase) {
    const snap = await fs.getDocs(fs.collection(firestore, 'people'));
    return snap.docs.map(d => d.data().name).sort();
  }
  return JSON.parse(localStorage.getItem(PEOPLE_KEY) || '[]').sort();
}

// ============================================================
// AI ANNOTATIONS IMPORT
// ============================================================
export async function importAIAnnotations(aiData) {
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
