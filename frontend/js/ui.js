/**
 * ui.js
 *
 * All shared UI utilities used across the app:
 * - Tab switching with history API support
 * - Toast notification system
 * - Loading / spinner state management
 * - Word count updater
 * - No-profile banner visibility
 * - User info display in nav
 * - Settings panel (API key save/delete)
 *
 * Exports used by other JS modules and app.html:
 *   initUI, switchTab, toast, setLoading, updateWordCount,
 *   showNoProfileBanner, hideNoProfileBanner,
 *   renderUserNav, initSettings
 */

import { getAuthHeaders, getBackendUrl, getUser, signOut } from './auth.js';

// ── Toast ──────────────────────────────────────────────────────────────────

let _toastTimer = null;

/**
 * Shows a toast notification.
 * @param {string} message
 * @param {'success'|'error'|'warning'|''} type
 * @param {number} duration - ms to display (default 3000)
 */
export function toast(message, type = '', duration = 3000) {
  const el = document.getElementById('toast');
  if (!el) return;

  el.textContent = message;
  el.className   = `show${type ? ' ' + type : ''}`;

  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    el.classList.remove('show', 'success', 'error', 'warning');
  }, duration);
}


// ── Tab switching ──────────────────────────────────────────────────────────

let _currentTab = 'humanize';
const _tabCallbacks = {}; // registered per-tab callbacks

/**
 * Initialises tabs on page load.
 * Reads the hash from the URL so deep-linking works (e.g. app.html#profile).
 */
export function initUI() {
  // Wire tab buttons
  document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Restore tab from URL hash
  const hash = window.location.hash.replace('#', '');
  const validTabs = ['humanize', 'setup', 'profile', 'history', 'settings'];
  const initialTab = validTabs.includes(hash) ? hash : 'humanize';
  switchTab(initialTab, false); // false = don't push to history

  // Update hash on popstate (browser back/forward)
  window.addEventListener('popstate', () => {
    const tab = window.location.hash.replace('#', '') || 'humanize';
    switchTab(tab, false);
  });

  // Sign out button
  document.getElementById('signOutBtn')
    ?.addEventListener('click', async () => {
      await signOut();
    });

  // Input textarea word count
  document.getElementById('inputText')
    ?.addEventListener('input', _updateInputWordCount);
}

/**
 * Switches the visible tab.
 * @param {string} name - Tab name matching data-tab attribute
 * @param {boolean} pushState - Whether to push to browser history (default true)
 */
export function switchTab(name, pushState = true) {
  // Hide all views
  document.querySelectorAll('.view[data-view]').forEach(v => {
    v.classList.remove('active');
  });

  // Deactivate all tab buttons
  document.querySelectorAll('.tab-btn[data-tab]').forEach(b => {
    b.classList.remove('active');
  });

  // Activate the target view and button
  const view = document.querySelector(`.view[data-view="${name}"]`);
  const btn  = document.querySelector(`.tab-btn[data-tab="${name}"]`);

  if (view) view.classList.add('active');
  if (btn)  btn.classList.add('active');

  _currentTab = name;

  // Update URL hash
  if (pushState) {
    history.pushState(null, '', `#${name}`);
  }

  // Fire per-tab callback if registered
  if (_tabCallbacks[name]) {
    _tabCallbacks[name]();
  }
}

/**
 * Registers a callback to fire when a tab is opened.
 * Used by history.js to refresh the list, profile.js to re-render, etc.
 *
 * @param {string} tabName
 * @param {Function} callback
 */
export function onTabOpen(tabName, callback) {
  _tabCallbacks[tabName] = callback;
}

/**
 * Returns the currently active tab name.
 * @returns {string}
 */
export function getCurrentTab() {
  return _currentTab;
}


// ── Loading state ──────────────────────────────────────────────────────────

/**
 * Toggles the loading state of the humanize button and loader indicator.
 * @param {boolean} loading
 * @param {string} message - Text to show in the loader
 */
export function setLoading(loading, message = 'Processing...') {
  const btn      = document.getElementById('humanizeBtn');
  const loader   = document.getElementById('loader');
  const loaderTx = document.getElementById('loaderText');
  const cancelBtn = document.getElementById('cancelBtn');

  if (btn)      btn.disabled      = loading;
  if (cancelBtn) cancelBtn.style.display = loading ? 'inline-flex' : 'none';

  if (loading) {
    loader?.classList.add('active');
    if (loaderTx) loaderTx.textContent = message;
  } else {
    loader?.classList.remove('active');
  }
}


// ── Word count ─────────────────────────────────────────────────────────────

function _countWords(text) {
  return (text.match(/\S+/g) || []).length;
}

function _readTime(words) {
  return Math.max(1, Math.round(words / 200));
}

function _updateInputWordCount() {
  const text  = document.getElementById('inputText')?.value || '';
  const words = _countWords(text);
  const chars = text.length;

  _setText('inputWords',    `${words} words`);
  _setText('inputChars',    `${chars} chars`);
  _setText('inputReadTime', `~${_readTime(words)} min read`);
}

/**
 * Updates the output pane word count bar.
 * @param {string} text
 */
export function updateWordCount(text) {
  const words = _countWords(text);
  const chars = text.length;

  _setText('outputWords',    `${words} words`);
  _setText('outputChars',    `${chars} chars`);
  _setText('outputReadTime', `~${_readTime(words)} min read`);

  const bar = document.getElementById('outputWCBar');
  if (bar) bar.style.display = 'flex';
}


// ── No-profile banner ──────────────────────────────────────────────────────

/**
 * Shows the "no profile" warning banner on the humanize tab.
 */
export function showNoProfileBanner() {
  document.getElementById('noProfileBanner')?.classList.add('show');
}

/**
 * Hides the "no profile" warning banner.
 */
export function hideNoProfileBanner() {
  document.getElementById('noProfileBanner')?.classList.remove('show');
}


// ── User nav display ───────────────────────────────────────────────────────

/**
 * Renders the signed-in user's info in the nav bar.
 * Shows their email (or just first name) and a sign-out button.
 */
export function renderUserNav() {
  const user     = getUser();
  const emailEl  = document.getElementById('userEmail');
  const avatarEl = document.getElementById('userAvatar');

  if (!user) return;

  if (emailEl) {
    // Show just the part before @ to save space in nav
    const shortName = user.email?.split('@')[0] || 'User';
    emailEl.textContent = shortName;
    emailEl.title       = user.email || '';
  }

  if (avatarEl && user.user_metadata?.avatar_url) {
    avatarEl.src           = user.user_metadata.avatar_url;
    avatarEl.style.display = 'block';
  }
}


// ── Settings panel ─────────────────────────────────────────────────────────

/**
 * Initialises the settings panel:
 * - Checks and displays current API key status
 * - Wires save / delete key buttons
 */
export async function initSettings() {
  await _refreshKeyStatus();

  document.getElementById('saveKeyBtn')
    ?.addEventListener('click', _saveApiKey);

  document.getElementById('deleteKeyBtn')
    ?.addEventListener('click', _deleteApiKey);
}

async function _refreshKeyStatus() {
  const statusEl = document.getElementById('keyStatus');
  if (!statusEl) return;

  // Retry once after a short delay to handle the race condition where
  // initAuth() hasn't fully restored the session by the time this runs.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(`${getBackendUrl()}/api/key/status`, {
        headers: getAuthHeaders(),  // throws if session not ready
      });
      const data = await response.json();

      if (data.has_key) {
        statusEl.textContent = 'API key configured';
        statusEl.className   = 'key-status has-key';
      } else {
        statusEl.textContent = 'No API key saved';
        statusEl.className   = 'key-status';
      }
      return; // success — stop retrying

    } catch (err) {
      if (attempt === 0) {
        // First attempt failed — wait 800ms and try once more
        await new Promise(r => setTimeout(r, 800));
      } else {
        // Both attempts failed — show a clear error, not "Checking..."
        statusEl.textContent = 'Could not reach server';
        statusEl.className   = 'key-status';
      }
    }
  }
}

async function _saveApiKey() {
  const input = document.getElementById('apiKeyInput');
  const key   = input?.value?.trim();

  if (!key || key.length < 10) {
    toast('Enter a valid Gemini API key.', 'error');
    return;
  }

  const btn = document.getElementById('saveKeyBtn');
  if (btn) btn.disabled = true;

  try {
    const response = await fetch(`${getBackendUrl()}/api/key`, {
      method:  'POST',
      headers: getAuthHeaders(),
      body:    JSON.stringify({ api_key: key }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to save key');
    }

    if (input) input.value = '';
    await _refreshKeyStatus();
    toast('API key saved securely.', 'success');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _deleteApiKey() {
  if (!confirm('Delete your stored API key? You will need to re-add it to use the app.')) return;

  try {
    const response = await fetch(`${getBackendUrl()}/api/key`, {
      method:  'DELETE',
      headers: getAuthHeaders(),
    });

    if (!response.ok) throw new Error('Failed to delete key');

    await _refreshKeyStatus();
    toast('API key deleted.', '');
  } catch (err) {
    toast(err.message, 'error');
  }
}


// ── Private helpers ────────────────────────────────────────────────────────

function _setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
