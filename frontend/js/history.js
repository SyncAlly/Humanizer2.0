/**
 * history.js
 *
 * Manages the rewrite history — a log of every (input, output) pair
 * from the current and previous sessions.
 *
 * Storage: localStorage under the key 'humanizer_history'.
 * The history is per-browser, not per-account — it's local convenience
 * data, not sensitive, so localStorage is appropriate here (unlike tokens).
 *
 * Each history item:
 * {
 *   id:       string   (timestamp-based unique ID)
 *   ts:       string   (ISO timestamp)
 *   input:    string   (original text)
 *   output:   string   (humanized text)
 *   strength: string   ('light' | 'medium' | 'aggressive')
 *   accepted: boolean  (whether user clicked Accept)
 * }
 *
 * Exports used by humanize.js and app.html:
 *   addHistoryItem, markAccepted, renderHistory,
 *   clearHistory, restoreHistoryItem
 */

import { toast } from './ui.js';

// ── Constants ──────────────────────────────────────────────────────────────

const STORAGE_KEY = 'humanizer_history';
const MAX_ITEMS   = 50; // keep last 50 rewrites


// ── In-memory cache ────────────────────────────────────────────────────────
// Loaded from localStorage once on module init, then kept in sync.

let _history = _loadFromStorage();


// ── Public API ─────────────────────────────────────────────────────────────

/**
 * Adds a new item to the history.
 * Called by humanize.js after a successful rewrite.
 *
 * @param {{ input: string, output: string, strength: string }} item
 */
export function addHistoryItem({ input, output, strength }) {
  const entry = {
    id:       `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    ts:       new Date().toISOString(),
    input:    input.trim(),
    output:   output.trim(),
    strength: strength || 'medium',
    accepted: false,
  };

  _history.unshift(entry);

  // Trim to max size
  if (_history.length > MAX_ITEMS) {
    _history = _history.slice(0, MAX_ITEMS);
  }

  _saveToStorage();

  // Update the history tab count badge
  _updateHistoryBadge();
}


/**
 * Marks a history item as accepted (user clicked "Accept & learn").
 * @param {string} id - Item ID
 */
export function markAccepted(id) {
  const item = _history.find(h => h.id === id);
  if (item) {
    item.accepted = true;
    _saveToStorage();
  }
}


/**
 * Renders the full history list into #historyList.
 * Called when the History tab is opened.
 */
export function renderHistory() {
  const emptyEl = document.getElementById('historyEmpty');
  const listEl  = document.getElementById('historyList');
  if (!emptyEl || !listEl) return;

  if (_history.length === 0) {
    emptyEl.style.display = 'block';
    listEl.innerHTML      = '';
    return;
  }

  emptyEl.style.display = 'none';
  listEl.innerHTML = _history.map((item, index) => _renderItem(item, index)).join('');
}


/**
 * Restores a history item to the humanize pane.
 * Called when the user clicks a history entry.
 *
 * @param {string} id - Item ID
 */
export function restoreHistoryItem(id) {
  const item = _history.find(h => h.id === id);
  if (!item) return;

  // Switch to humanize tab
  const humanizeTab = document.querySelector('[data-tab="humanize"]');
  if (humanizeTab) humanizeTab.click();

  // Populate input pane
  const inputEl = document.getElementById('inputText');
  if (inputEl) {
    inputEl.value = item.input;
    inputEl.dispatchEvent(new Event('input')); // trigger word count update
  }

  // Populate output pane
  const outputEl = document.getElementById('outputContent');
  if (outputEl) {
    outputEl.classList.remove('empty');
    // Use humanize module's renderer via a custom event
    // (avoids circular import — humanize.js listens for this)
    document.dispatchEvent(new CustomEvent('humanizer:restoreOutput', {
      detail: { input: item.input, output: item.output },
    }));
  }

  // Set strength to match the original rewrite
  document.dispatchEvent(new CustomEvent('humanizer:setStrength', {
    detail: { strength: item.strength },
  }));

  // Show feedback row
  const feedbackRow = document.getElementById('feedbackRow');
  if (feedbackRow) feedbackRow.style.display = 'flex';

  toast('Restored from history.', 'success');
}


/**
 * Clears all history after user confirmation.
 */
export function clearHistory() {
  if (!confirm('Clear all rewrite history?')) return;
  _history = [];
  _saveToStorage();
  renderHistory();
  _updateHistoryBadge();
  toast('History cleared.', '');
}


/**
 * Returns the number of history items.
 * @returns {number}
 */
export function getHistoryCount() {
  return _history.length;
}


// ── Private: render a single history item ─────────────────────────────────

function _renderItem(item, index) {
  const date     = new Date(item.ts);
  const timeStr  = date.toLocaleString(undefined, {
    month:  'short',
    day:    'numeric',
    hour:   '2-digit',
    minute: '2-digit',
  });

  const inputPreview  = _truncate(item.input,  180);
  const outputPreview = _truncate(item.output, 180);

  return `
    <div class="history-item" onclick="restoreHistoryItemById('${item.id}')">
      <div class="history-meta">
        <span class="history-time">${_escapeHTML(timeStr)}</span>
        <div class="row" style="gap:var(--sp-2)">
          ${item.accepted
            ? '<span class="badge badge-green" style="font-size:10px;padding:2px 8px">accepted</span>'
            : ''}
          <span class="badge" style="font-size:10px;padding:2px 8px">${item.strength}</span>
        </div>
      </div>
      <div class="history-cols">
        <div>
          <div class="history-col-label">Input</div>
          <div class="history-text">${_escapeHTML(inputPreview)}</div>
        </div>
        <div>
          <div class="history-col-label">Output</div>
          <div class="history-text output">${_escapeHTML(outputPreview)}</div>
        </div>
      </div>
    </div>`;
}


// ── Private: storage helpers ───────────────────────────────────────────────

function _loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function _saveToStorage() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(_history));
  } catch (err) {
    // localStorage full — silently fail, history is non-critical
    console.warn('[history] localStorage write failed:', err);
  }
}

function _updateHistoryBadge() {
  const badge = document.getElementById('historyCountBadge');
  if (!badge) return;
  const count = _history.length;
  badge.textContent = count > 0 ? String(count) : '';
  badge.style.display = count > 0 ? 'inline' : 'none';
}


// ── Private: string helpers ────────────────────────────────────────────────

function _truncate(str, maxLen) {
  if (!str) return '';
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str;
}

function _escapeHTML(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}


// ── Global bridge function ─────────────────────────────────────────────────
// app.html calls this from inline onclick — can't use module imports there.
// Attached to window so it's accessible from HTML onclick attributes.

window.restoreHistoryItemById = function(id) {
  restoreHistoryItem(id);
};
