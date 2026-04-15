/**
 * Family Photo Album - Main Application
 *
 * Single-page app with three views: Gallery, Detail, Slideshow.
 * Hash-based routing: #/gallery, #/photo/<filename>, #/slideshow
 */
import { CONFIG } from './config.js';
import * as store from './store.js';

// ============================================================
// STATE
// ============================================================
const state = {
  images: [],
  annotations: {},
  people: [],
  filteredImages: [],
  filterPerson: null,
  searchQuery: '',
  currentPhoto: null,
  currentIndex: -1,
  // Slideshow
  shuffled: [],
  slideIndex: -1,
  paused: false,
  autoTimer: null,
};

// ============================================================
// DOM HELPERS
// ============================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function escapeHtml(str) {
  if (!str) return '';
  const el = document.createElement('span');
  el.textContent = str;
  return el.innerHTML;
}

function escapeAttr(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ============================================================
// IMAGE URL HELPERS
// ============================================================
function getImageUrl(filename, thumb = false) {
  if (CONFIG.imageSource === 'r2') {
    const path = thumb ? CONFIG.r2.thumbPath : CONFIG.r2.fullPath;
    return `${CONFIG.r2.publicUrl}${path}/${encodeURIComponent(filename)}`;
  }
  const base = thumb ? CONFIG.localThumbPath : CONFIG.localImagePath;
  return `${base}${encodeURIComponent(filename)}`;
}

// ============================================================
// DATA LOADING
// ============================================================
async function loadManifest() {
  try {
    // When using R2, fetch manifest from the R2 bucket directly
    const url = CONFIG.imageSource === 'r2'
      ? `${CONFIG.r2.publicUrl}/manifest.json`
      : './manifest.json';
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('No manifest');
    const data = await resp.json();
    return data.images || [];
  } catch {
    console.warn('[app] No manifest.json found. Run: python build.py');
    return [];
  }
}

async function loadAIAnnotations() {
  try {
    const resp = await fetch('./annotations.json');
    if (resp.ok) {
      const data = await resp.json();
      const count = await store.importAIAnnotations(data);
      if (count > 0) console.log(`[app] Imported ${count} AI annotations`);
    }
  } catch {
    // annotations.json is optional
  }
}

// ============================================================
// VIEW MANAGEMENT
// ============================================================
function showView(name) {
  $$('.view').forEach(v => v.classList.remove('active'));
  const view = $(`#${name}-view`);
  if (view) view.classList.add('active');

  $$('#app-header nav button').forEach(b => {
    b.classList.toggle('active', b.dataset.view === name);
  });

  $('#app-header').style.display = name === 'slideshow' ? 'none' : '';

  if (name !== 'slideshow') {
    stopSlideshow();
  }
}

// ============================================================
// ROUTER
// ============================================================
function handleRoute() {
  const hash = window.location.hash || '#/gallery';

  if (hash.startsWith('#/photo/')) {
    const filename = decodeURIComponent(hash.slice(8));
    showView('detail');
    renderDetail(filename);
  } else if (hash === '#/slideshow') {
    showView('slideshow');
    startSlideshow();
  } else {
    showView('gallery');
    renderGallery();
  }
}

function navigate(hash) {
  window.location.hash = hash;
}

// ============================================================
// GALLERY VIEW
// ============================================================
function filterImages() {
  let filtered = [...state.images];

  if (state.filterPerson) {
    filtered = filtered.filter(img => {
      const ann = state.annotations[img];
      return ann && ann.people && ann.people.includes(state.filterPerson);
    });
  }

  if (state.searchQuery) {
    const q = state.searchQuery.toLowerCase();
    filtered = filtered.filter(img => {
      if (img.toLowerCase().includes(q)) return true;
      const ann = state.annotations[img];
      if (!ann) return false;
      if (ann.ai) {
        const aiFields = [ann.ai.scene, ann.ai.setting, ann.ai.occasion, ann.ai.decade, ann.ai.mood, ann.ai.people_description];
        if (aiFields.some(v => v && v.toLowerCase().includes(q))) return true;
      }
      if (ann.corrections) {
        if (Object.values(ann.corrections).some(v => v && String(v).toLowerCase().includes(q))) return true;
      }
      if (ann.people && ann.people.some(p => p.toLowerCase().includes(q))) return true;
      if (ann.anecdotes && ann.anecdotes.some(a => a.text.toLowerCase().includes(q))) return true;
      return false;
    });
  }

  state.filteredImages = filtered;
}

function renderGallery() {
  filterImages();

  const grid = $('#gallery-grid');
  const stats = $('#gallery-stats');

  if (state.images.length === 0) {
    grid.innerHTML = `<div class="empty-state">
      <p>No photos loaded yet</p>
      <small>Run <code>python build.py</code> to generate the image manifest</small>
    </div>`;
    stats.textContent = '';
    renderPeopleFilter();
    return;
  }

  stats.textContent = state.filteredImages.length === state.images.length
    ? `${state.images.length} photos`
    : `${state.filteredImages.length} of ${state.images.length} photos`;

  grid.innerHTML = state.filteredImages.map(img => {
    const ann = state.annotations[img] || {};
    const people = (ann.people || []).join(', ');
    let statusClass = 'status-none';
    let statusText = '';
    if (ann.confirmed) {
      statusClass = 'status-confirmed';
      statusText = '\u2713';
    } else if (ann.ai) {
      statusClass = 'status-ai-draft';
      statusText = 'AI';
    }

    return `<div class="gallery-item" data-photo="${escapeAttr(img)}">
      <img src="${getImageUrl(img, true)}" loading="lazy" alt="">
      <div class="gallery-item-overlay">
        <span class="gallery-item-people">${escapeHtml(people)}</span>
        ${statusText ? `<span class="gallery-item-status ${statusClass}">${statusText}</span>` : ''}
      </div>
    </div>`;
  }).join('');

  renderPeopleFilter();
}

function renderPeopleFilter() {
  const container = $('#people-filter');
  if (state.people.length === 0) {
    container.innerHTML = '';
    return;
  }

  const counts = {};
  state.people.forEach(p => { counts[p] = 0; });
  Object.values(state.annotations).forEach(ann => {
    (ann.people || []).forEach(p => {
      if (counts[p] !== undefined) counts[p]++;
    });
  });

  container.innerHTML = state.people.map(p =>
    `<span class="person-chip ${state.filterPerson === p ? 'active' : ''}" data-person="${escapeAttr(p)}">
      ${escapeHtml(p)} <span class="chip-count">${counts[p] || 0}</span>
    </span>`
  ).join('');
}

// ============================================================
// DETAIL VIEW
// ============================================================
async function renderDetail(filename) {
  state.currentPhoto = filename;
  state.currentIndex = state.filteredImages.indexOf(filename);
  if (state.currentIndex === -1) {
    state.currentIndex = state.images.indexOf(filename);
    if (state.filteredImages.length !== state.images.length) {
      state.filteredImages = [...state.images];
    }
  }

  $('#detail-photo').src = getImageUrl(filename);
  $('#detail-photo').alt = filename;

  const ann = await store.getAnnotation(filename);
  state.annotations[filename] = ann;

  renderAnnotationPanel(ann);
  renderPeopleTags(ann);
  renderAnecdotes(ann);
}

function renderAnnotationPanel(ann) {
  const content = $('#ai-annotation-content');
  const status = $('#annotation-status');
  const actions = $('#annotation-actions');

  if (ann.confirmed) {
    status.textContent = 'Confirmed';
    status.className = 'badge status-confirmed';
  } else if (ann.ai) {
    status.textContent = 'AI Draft';
    status.className = 'badge status-ai-draft';
  } else {
    status.textContent = '';
    status.className = 'badge';
  }

  if (!ann.ai) {
    content.innerHTML = `<p style="color:var(--text-light);font-size:0.85rem">
      No AI annotation yet. Run <code>python ai_annotate.py</code> to generate descriptions.
    </p>`;
    actions.style.display = 'none';
    return;
  }

  actions.style.display = '';
  const corrections = ann.corrections || {};

  const fields = [
    { key: 'scene', label: 'Scene' },
    { key: 'decade', label: 'Era' },
    { key: 'occasion', label: 'Occasion' },
    { key: 'setting', label: 'Setting' },
    { key: 'people_description', label: 'People' },
    { key: 'mood', label: 'Mood' },
  ];

  content.innerHTML = fields.map(f => {
    const original = ann.ai[f.key] || '';
    const corrected = corrections[f.key];
    if (!original && !corrected) return '';

    return `<div class="ai-field">
      <div class="ai-field-label">${f.label}</div>
      ${corrected
        ? `<div class="ai-field-value corrected">${escapeHtml(original)}</div>
           <div class="ai-field-correction">${escapeHtml(corrected)}</div>`
        : `<div class="ai-field-value">${escapeHtml(original)}</div>`}
    </div>`;
  }).join('');
}

function renderPeopleTags(ann) {
  const container = $('#people-tags');
  const people = ann.people || [];

  container.innerHTML = people.length > 0
    ? people.map(p =>
        `<span class="person-tag">${escapeHtml(p)}
          <span class="remove-person" data-person="${escapeAttr(p)}">&times;</span>
        </span>`
      ).join('')
    : '<span style="color:var(--text-light);font-size:0.85rem">No one tagged yet</span>';
}

function renderAnecdotes(ann) {
  const container = $('#anecdotes-list');
  const anecdotes = ann.anecdotes || [];

  if (anecdotes.length === 0) {
    container.innerHTML = '<p style="color:var(--text-light);font-size:0.85rem;margin-bottom:12px">No stories yet — be the first to share!</p>';
    return;
  }

  const currentEmail = store.getCurrentUserEmail();
  const currentName = store.getCurrentUser();
  const isAdmin = store.getCurrentUserRole() === 'admin';

  container.innerHTML = anecdotes.map((a, i) => {
    const date = new Date(a.timestamp).toLocaleDateString();
    // Prefer email match (reliable) and fall back to name (legacy records
    // and localStorage-only mode).
    const isMine = a.authorEmail
      ? a.authorEmail === currentEmail
      : a.author === currentName;
    const canDelete = isMine || isAdmin;
    return `<div class="anecdote-card">
      <div class="anecdote-text">\u201C${escapeHtml(a.text)}\u201D</div>
      <div class="anecdote-meta">
        <span>\u2014 ${escapeHtml(a.author)}, ${date}</span>
        ${canDelete ? `<span class="anecdote-delete" data-index="${i}">&times;</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ============================================================
// EDIT / CORRECTION MODAL
// ============================================================
function showEditModal() {
  const ann = state.annotations[state.currentPhoto] || {};
  if (!ann.ai) return;

  const corrections = ann.corrections || {};
  const fields = [
    { key: 'scene', label: 'Scene Description', rows: 3 },
    { key: 'decade', label: 'Era / Decade' },
    { key: 'occasion', label: 'Occasion' },
    { key: 'setting', label: 'Setting / Location' },
    { key: 'people_description', label: 'People Description', rows: 2 },
    { key: 'mood', label: 'Mood' },
  ];

  const overlay = document.createElement('div');
  overlay.className = 'edit-overlay';
  overlay.innerHTML = `<div class="edit-form">
    <h3>Correct AI Description</h3>
    <p style="color:var(--text-light);font-size:0.85rem;margin-bottom:16px">
      Edit fields that need correction. Leave unchanged fields as-is.
    </p>
    ${fields.map(f => {
      const val = corrections[f.key] || ann.ai[f.key] || '';
      return `<label>${f.label}</label>
        ${f.rows
          ? `<textarea data-field="${f.key}" rows="${f.rows}">${escapeHtml(val)}</textarea>`
          : `<input type="text" data-field="${f.key}" value="${escapeAttr(val)}">`}`;
    }).join('')}
    <div class="edit-form-actions">
      <button class="btn-cancel" id="edit-cancel">Cancel</button>
      <button class="btn-save" id="edit-save">Save Corrections</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);

  overlay.querySelector('#edit-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });

  overlay.querySelector('#edit-save').addEventListener('click', async () => {
    const newCorrections = {};
    let hasChanges = false;
    overlay.querySelectorAll('[data-field]').forEach(el => {
      const key = el.dataset.field;
      const val = el.value.trim();
      const original = ann.ai[key] || '';
      if (val && val !== original) {
        newCorrections[key] = val;
        hasChanges = true;
      }
    });

    if (hasChanges) {
      await store.saveCorrection(state.currentPhoto, newCorrections);
      const updated = await store.getAnnotation(state.currentPhoto);
      state.annotations[state.currentPhoto] = updated;
      renderAnnotationPanel(updated);
    }
    overlay.remove();
  });
}

// ============================================================
// PERSON NAME SUGGESTIONS
// ============================================================
function showPersonSuggestions(query) {
  const container = $('#person-suggestions');
  if (!query) {
    container.style.display = 'none';
    return;
  }

  const q = query.toLowerCase();
  const currentPeople = state.annotations[state.currentPhoto]?.people || [];
  const matches = state.people.filter(p =>
    p.toLowerCase().includes(q) && !currentPeople.includes(p)
  );

  if (matches.length === 0) {
    container.style.display = 'none';
    return;
  }

  const input = $('#person-input');
  const rect = input.getBoundingClientRect();
  container.style.cssText = `display:block; position:fixed; top:${rect.bottom + 2}px; left:${rect.left}px; width:${rect.width}px;`;

  container.innerHTML = matches.map(p =>
    `<div class="suggestion" data-person="${escapeAttr(p)}">${escapeHtml(p)}</div>`
  ).join('');
}

// ============================================================
// SLIDESHOW
// ============================================================
let activeSlideEl, nextSlideEl;

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function startSlideshow() {
  const images = state.filteredImages.length > 0 ? state.filteredImages : state.images;
  if (images.length === 0) return;

  state.shuffled = shuffle(images);
  state.slideIndex = -1;
  state.paused = false;

  activeSlideEl = $('#slide-a');
  nextSlideEl = $('#slide-b');
  activeSlideEl.classList.remove('active');
  nextSlideEl.classList.remove('active');

  document.body.style.cursor = 'none';

  advanceSlide();
  resetSlideshowTimer();
}

function stopSlideshow() {
  if (state.autoTimer) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
  }
  document.body.style.cursor = '';
  $('#pause-indicator').style.opacity = '0';
  $('#slideshow-annotation-overlay').classList.remove('visible');
}

function advanceSlide() {
  state.slideIndex = (state.slideIndex + 1) % state.shuffled.length;
  renderSlide(state.slideIndex);
}

function retreatSlide() {
  state.slideIndex = (state.slideIndex - 1 + state.shuffled.length) % state.shuffled.length;
  renderSlide(state.slideIndex);
}

function renderSlide(index) {
  const filename = state.shuffled[index];
  const src = getImageUrl(filename);

  nextSlideEl.style.backgroundImage = `url('${src}')`;
  nextSlideEl.classList.add('active');
  activeSlideEl.classList.remove('active');
  [activeSlideEl, nextSlideEl] = [nextSlideEl, activeSlideEl];

  $('#counter').textContent = `${index + 1} / ${state.shuffled.length}`;

  if (CONFIG.slideshow.showAnnotations) {
    showSlideshowAnnotation(filename);
  }

  // Preload ahead
  for (let i = 1; i <= CONFIG.slideshow.preloadCount; i++) {
    const idx = (index + i) % state.shuffled.length;
    new Image().src = getImageUrl(state.shuffled[idx]);
  }
}

function showSlideshowAnnotation(filename) {
  const overlay = $('#slideshow-annotation-overlay');
  const ann = state.annotations[filename];

  if (!ann || (!ann.ai && !(ann.people && ann.people.length))) {
    overlay.classList.remove('visible');
    return;
  }

  const parts = [];
  const corrections = ann.corrections || {};

  if (corrections.scene || ann.ai?.scene) parts.push(corrections.scene || ann.ai.scene);
  if (ann.people && ann.people.length) parts.push(ann.people.join(', '));
  if (corrections.decade || ann.ai?.decade) parts.push(corrections.decade || ann.ai.decade);

  if (parts.length === 0) {
    overlay.classList.remove('visible');
    return;
  }

  overlay.textContent = parts.join('  \u00B7  ');
  overlay.classList.add('visible');

  setTimeout(() => overlay.classList.remove('visible'), CONFIG.slideshow.annotationDisplayMs);
}

function resetSlideshowTimer() {
  if (state.autoTimer) clearInterval(state.autoTimer);
  if (!state.paused) {
    state.autoTimer = setInterval(advanceSlide, CONFIG.slideshow.autoAdvanceMs);
  }
}

function toggleSlideshowPause() {
  state.paused = !state.paused;
  $('#pause-indicator').style.opacity = state.paused ? '1' : '0';
  if (state.paused) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
  } else {
    resetSlideshowTimer();
  }
}

// ============================================================
// USER MODAL
// ============================================================
function showUserModal() {
  $('#user-modal').classList.add('active');
  setTimeout(() => $('#user-name-input').focus(), 100);
}

function hideUserModal() {
  $('#user-modal').classList.remove('active');
}

async function submitUserName() {
  const name = $('#user-name-input').value.trim();
  if (!name) return;
  const submitBtn = $('#user-name-submit');
  const errorEl = $('#user-name-error');
  if (errorEl) errorEl.textContent = '';
  submitBtn.disabled = true;
  try {
    await store.setCurrentUser(name);
    $('#user-name-display').textContent = name;
    hideUserModal();
  } catch (err) {
    console.error('[app] Failed to save display name:', err);
    if (errorEl) {
      errorEl.textContent =
        "Sorry, we couldn't save your name. Please try again in a moment.";
    }
  } finally {
    submitBtn.disabled = false;
  }
}

// ============================================================
// EVENT LISTENERS
// ============================================================
function setupEvents() {
  // Routing
  window.addEventListener('hashchange', handleRoute);

  // Nav buttons
  $$('#app-header nav button').forEach(btn => {
    btn.addEventListener('click', () => navigate(`#/${btn.dataset.view}`));
  });

  // User modal
  $('#user-name-submit').addEventListener('click', () => { submitUserName(); });
  $('#user-name-input').addEventListener('keydown', e => { if (e.key === 'Enter') submitUserName(); });
  $('#change-user').addEventListener('click', showUserModal);

  // Gallery: click photo → detail
  $('#gallery-grid').addEventListener('click', e => {
    const item = e.target.closest('.gallery-item');
    if (item) navigate(`#/photo/${encodeURIComponent(item.dataset.photo)}`);
  });

  // Gallery: search
  let searchTimeout;
  $('#search-input').addEventListener('input', e => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      state.searchQuery = e.target.value;
      renderGallery();
    }, 200);
  });

  // Gallery: people filter chips
  $('#people-filter').addEventListener('click', e => {
    const chip = e.target.closest('.person-chip');
    if (!chip) return;
    state.filterPerson = state.filterPerson === chip.dataset.person ? null : chip.dataset.person;
    renderGallery();
  });

  // Detail: prev/next
  $('#detail-prev').addEventListener('click', () => navigateDetail(-1));
  $('#detail-next').addEventListener('click', () => navigateDetail(1));

  // Detail: confirm
  $('#btn-confirm').addEventListener('click', async () => {
    if (!state.currentPhoto) return;
    await store.confirmAnnotation(state.currentPhoto);
    const updated = await store.getAnnotation(state.currentPhoto);
    state.annotations[state.currentPhoto] = updated;
    renderAnnotationPanel(updated);
  });

  // Detail: edit
  $('#btn-edit').addEventListener('click', showEditModal);

  // Detail: add person
  async function addPersonFromInput() {
    const input = $('#person-input');
    const name = input.value.trim();
    if (!name || !state.currentPhoto) return;
    await store.tagPerson(state.currentPhoto, name);
    const updated = await store.getAnnotation(state.currentPhoto);
    state.annotations[state.currentPhoto] = updated;
    state.people = await store.getAllPeople();
    renderPeopleTags(updated);
    input.value = '';
    $('#person-suggestions').style.display = 'none';
  }

  $('#btn-add-person').addEventListener('click', addPersonFromInput);
  $('#person-input').addEventListener('keydown', e => { if (e.key === 'Enter') addPersonFromInput(); });
  $('#person-input').addEventListener('input', e => showPersonSuggestions(e.target.value));

  // Person suggestion click
  $('#person-suggestions').addEventListener('click', e => {
    const sug = e.target.closest('.suggestion');
    if (sug) {
      $('#person-input').value = sug.dataset.person;
      addPersonFromInput();
    }
  });

  // Remove person tag
  $('#people-tags').addEventListener('click', async e => {
    const btn = e.target.closest('.remove-person');
    if (!btn || !state.currentPhoto) return;
    await store.untagPerson(state.currentPhoto, btn.dataset.person);
    const updated = await store.getAnnotation(state.currentPhoto);
    state.annotations[state.currentPhoto] = updated;
    renderPeopleTags(updated);
  });

  // Add anecdote
  $('#btn-add-anecdote').addEventListener('click', async () => {
    const input = $('#anecdote-input');
    const text = input.value.trim();
    const author = store.getCurrentUser();
    if (!text || !author || !state.currentPhoto) {
      if (!author) showUserModal();
      return;
    }
    await store.addAnecdote(state.currentPhoto, author, text);
    const updated = await store.getAnnotation(state.currentPhoto);
    state.annotations[state.currentPhoto] = updated;
    renderAnecdotes(updated);
    input.value = '';
  });

  // Delete anecdote
  $('#anecdotes-list').addEventListener('click', async e => {
    const btn = e.target.closest('.anecdote-delete');
    if (!btn || !state.currentPhoto) return;
    await store.deleteAnecdote(state.currentPhoto, parseInt(btn.dataset.index));
    const updated = await store.getAnnotation(state.currentPhoto);
    state.annotations[state.currentPhoto] = updated;
    renderAnecdotes(updated);
  });

  // Slideshow: exit
  $('#slideshow-exit').addEventListener('click', () => navigate('#/gallery'));

  // Slideshow: click to pause (but not on exit button)
  $('#slideshow-view').addEventListener('click', e => {
    if (e.target.id === 'slideshow-exit') return;
    toggleSlideshowPause();
  });

  // Keyboard
  document.addEventListener('keydown', e => {
    // Ignore when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    const hash = window.location.hash;

    if (hash === '#/slideshow') {
      switch (e.key) {
        case 'ArrowRight': case ' ':
          e.preventDefault(); advanceSlide(); resetSlideshowTimer(); break;
        case 'ArrowLeft':
          e.preventDefault(); retreatSlide(); resetSlideshowTimer(); break;
        case 'Escape':
          navigate('#/gallery'); break;
        case 'p':
          toggleSlideshowPause(); break;
      }
    } else if (hash.startsWith('#/photo/')) {
      switch (e.key) {
        case 'ArrowRight':
          e.preventDefault(); navigateDetail(1); break;
        case 'ArrowLeft':
          e.preventDefault(); navigateDetail(-1); break;
        case 'Escape':
          navigate('#/gallery'); break;
      }
    }
  });

  // Close suggestions on outside click
  document.addEventListener('click', e => {
    if (!e.target.closest('#add-person-form') && !e.target.closest('#person-suggestions')) {
      $('#person-suggestions').style.display = 'none';
    }
  });
}

function navigateDetail(direction) {
  const list = state.filteredImages.length > 0 ? state.filteredImages : state.images;
  if (list.length === 0) return;
  const idx = (state.currentIndex + direction + list.length) % list.length;
  navigate(`#/photo/${encodeURIComponent(list[idx])}`);
}

// ============================================================
// NOT-AUTHORIZED SCREEN
// ============================================================
function showNotAuthorized(message) {
  document.body.innerHTML = `
    <div class="auth-gate">
      <div class="auth-gate-card">
        <h1>Welcome to the Family Album</h1>
        <p class="auth-gate-message">${escapeHtml(message)}</p>
        <p class="auth-gate-hint">
          If you believe this is a mistake, please ask the album owner
          to add your email to the family list.
        </p>
      </div>
    </div>`;
}

// ============================================================
// INITIALIZATION
// ============================================================
async function init() {
  document.title = CONFIG.app.title;

  // Authenticate first. With a Worker API configured, identity comes
  // from Cloudflare Access. Without one, fall back to the localStorage
  // "what's your name?" prompt.
  const auth = await store.init();

  if (auth.mode === 'error') {
    showNotAuthorized(
      `We couldn't reach the album right now. Please try again in a minute. (${auth.error})`
    );
    return;
  }
  if (auth.mode === 'unauthenticated') {
    // Access session missing / cross-site cookie dropped. A top-level
    // reload will re-enter the Access login flow.
    showNotAuthorized(
      "Your sign-in has expired. Reload this page to sign in again."
    );
    return;
  }
  if (auth.mode === 'forbidden') {
    showNotAuthorized(
      auth.error || "You aren't on the album's allow-list yet."
    );
    return;
  }

  // 'api' (signed in via Access) and 'local' (localStorage) modes both
  // need a display name. Prompt on first login, show existing otherwise.
  const currentName = store.getCurrentUser();
  if (store.needsDisplayName()) {
    showUserModal();
  } else {
    $('#user-name-display').textContent = currentName;
  }

  state.images = await loadManifest();
  state.filteredImages = [...state.images];

  await loadAIAnnotations();

  state.annotations = await store.getAllAnnotations();
  state.people = await store.getAllPeople();

  setupEvents();
  handleRoute();
}

init().catch(err => console.error('[app] Init failed:', err));
