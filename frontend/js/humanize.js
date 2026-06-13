/**
 * humanize.js
 *
 * Handles the core humanize flow:
 * - Sending text + profile to the backend
 * - Reading the Server-Sent Events (SSE) stream chunk by chunk
 * - Updating the output pane in real time as tokens arrive
 * - Wiring the accept / reject feedback buttons
 * - Managing the rate limit display
 *
 * SSE stream format (from backend):
 *   data: {"text": "<chunk>"}       — partial output token
 *   data: {"done": true}            — stream complete
 *   data: {"error": "<message>"}    — something went wrong
 *   data: {"warning": "<message>"}  — non-fatal issue (e.g. safety filter)
 *
 * Exports used by app.html:
 *   initHumanize, humanize, setStrength, copyOutput
 */

import { getAuthHeaders, getBackendUrl } from './auth.js';
import {
  getProfile,
  acceptOutput  as acceptOutputInProfile,
  saveAcceptedSampleToBackend,
} from './profile.js';
import { toast, setLoading, updateWordCount } from './ui.js';
import { addHistoryItem } from './history.js';

// ── Module state ───────────────────────────────────────────────────────────

let _currentStrength  = 'medium';
let _lastInput        = '';
let _lastOutput       = '';
let _showDiff         = true;
let _isStreaming      = false;
let _abortController  = null;  // lets us cancel an in-flight stream


// ── Initialisation ─────────────────────────────────────────────────────────

/**
 * Call once when the app page loads.
 * Wires up UI event listeners and loads initial rate limit display.
 */
export function initHumanize() {
  // Strength buttons
  document.querySelectorAll('.strength-opt').forEach(btn => {
    btn.addEventListener('click', () => {
      setStrength(btn.dataset.strength, btn);
    });
  });

  // Diff toggle
  const diffToggle = document.getElementById('diffToggle');
  if (diffToggle) {
    diffToggle.addEventListener('change', () => {
      _showDiff = diffToggle.checked;
      if (_lastOutput) _renderOutput(_lastInput, _lastOutput);
    });
  }

  // Accept / reject buttons
  document.getElementById('acceptBtn')
    ?.addEventListener('click', handleAccept);
  document.getElementById('rejectBtn')
    ?.addEventListener('click', handleReject);

  // Rewrite again button
  document.getElementById('rewriteBtn')
    ?.addEventListener('click', () => {
      if (_lastInput) humanize(_lastInput);
    });

  // Copy button
  document.getElementById('copyBtn')
    ?.addEventListener('click', copyOutput);

  // Cancel button (shown during streaming)
  document.getElementById('cancelBtn')
    ?.addEventListener('click', cancelStream);

  // Load rate limit info on startup
  loadUsageStats();
}


// ── Core humanize function ─────────────────────────────────────────────────

/**
 * Main entry point — sends text to the backend and streams the response.
 *
 * @param {string} inputText - The text to humanize (defaults to textarea value)
 */
export async function humanize(inputText) {
  const text = (inputText || document.getElementById('inputText')?.value || '').trim();

  if (!text) {
    toast('Paste some text to humanize first.', 'error');
    return;
  }
  if (text.length > 8000) {
    toast('Text too long — keep it under 8,000 characters.', 'error');
    return;
  }
  if (_isStreaming) {
    toast('Already running — wait for the current rewrite to finish.', 'warning');
    return;
  }

  _lastInput  = text;
  _lastOutput = '';
  _isStreaming = true;

  // ── UI: enter loading state ──────────────────────────────────
  setLoading(true, 'Rewriting...');
  _setOutputState('streaming');
  _showFeedbackRow(false);
  document.getElementById('outputWCBar')
    && (document.getElementById('outputWCBar').style.display = 'none');

  const outputEl = document.getElementById('outputContent');
  outputEl.classList.remove('empty');
  outputEl.innerHTML = '<span class="cursor"></span>';

  // ── Build request body ───────────────────────────────────────
  const profile = getProfile();
  const body    = {
    text,
    strength: _currentStrength,
    profile:  profile || null,
  };

  // ── Abort controller for cancel support ─────────────────────
  _abortController = new AbortController();

  try {
    const response = await fetch(`${getBackendUrl()}/api/humanize`, {
      method:  'POST',
      headers: getAuthHeaders(),
      body:    JSON.stringify(body),
      signal:  _abortController.signal,
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      const msg = _mapStatusToMessage(response.status, errData.detail);
      throw new Error(msg);
    }

    // ── Read the SSE stream ──────────────────────────────────
    await _readStream(response, outputEl);

  } catch (err) {
    if (err.name === 'AbortError') {
      // User cancelled — show whatever was built so far
      if (_lastOutput) {
        _renderOutput(_lastInput, _lastOutput);
        toast('Cancelled — partial output shown.', 'warning');
        _showFeedbackRow(true);
      } else {
        _setOutputState('empty');
        toast('Cancelled.', '');
      }
    } else {
      console.error('[humanize] error:', err);
      _setOutputState('error', err.message);
      toast(err.message, 'error');
    }
  } finally {
    _isStreaming      = false;
    _abortController  = null;
    setLoading(false);
  }
}


// ── Stream reader ──────────────────────────────────────────────────────────

async function _readStream(response, outputEl) {
  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let   buffer  = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE events are separated by double newlines
    const events = buffer.split('\n\n');
    buffer = events.pop(); // last item may be incomplete — keep in buffer

    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith('data: ')) continue;

      let data;
      try {
        data = JSON.parse(line.slice(6));
      } catch {
        continue; // malformed chunk — skip
      }

      if (data.error) {
        throw new Error(data.error);
      }

      if (data.warning) {
        console.warn('[humanize] stream warning:', data.warning);
        toast(`Note: ${data.warning}`, 'warning');
      }

      if (data.text) {
        _lastOutput += data.text;
        // Render with streaming cursor still visible
        outputEl.innerHTML =
          (_showDiff ? _highlightChanges(_lastInput, _lastOutput) : _escapeHTML(_lastOutput))
          + '<span class="cursor"></span>';
        // Auto-scroll to bottom as text streams in
        outputEl.scrollTop = outputEl.scrollHeight;
      }

      if (data.done) {
        break;
      }
    }
  }

  // ── Stream finished ──────────────────────────────────────────
  if (_lastOutput) {
    _renderOutput(_lastInput, _lastOutput);
    _updateOutputWordCount(_lastOutput);
    _showFeedbackRow(true);

    // Enable rewrite button
    const rewriteBtn = document.getElementById('rewriteBtn');
    if (rewriteBtn) rewriteBtn.disabled = false;

    // Save to history
    addHistoryItem({
      input:    _lastInput,
      output:   _lastOutput,
      strength: _currentStrength,
    });

    // Refresh rate limit display
    loadUsageStats();

    toast('Done!', 'success');
  } else {
    _setOutputState('empty');
    toast('No output received — try again.', 'error');
  }
}


// ── Feedback: accept / reject ──────────────────────────────────────────────

/**
 * Accept the current output:
 * - Folds it into the local style profile
 * - Saves the updated profile to the backend
 * - Hides the feedback row
 */
export async function handleAccept() {
  if (!_lastOutput) return;

  const updatedProfile = acceptOutputInProfile(_lastOutput);
  _showFeedbackRow(false);

  // Save to backend (non-blocking — don't await to keep UI snappy)
  saveAcceptedSampleToBackend().then(ok => {
    if (ok) {
      toast('Accepted — profile updated.', 'success');
    } else {
      toast('Accepted locally — profile sync failed. Check connection.', 'warning');
    }
  });

  // Update profile stats display
  const { renderProfileStats, renderProfileDetail, getAcceptedCount } =
    await import('./profile.js');
  renderProfileStats(updatedProfile);
  renderProfileDetail(updatedProfile, getAcceptedCount());
}

/**
 * Reject the current output — just dismiss the feedback row.
 * The user will typically adjust strength and try again.
 */
export function handleReject() {
  _showFeedbackRow(false);
  toast('Rejected — try a higher strength or add more samples.', '');
}


// ── Cancel stream ──────────────────────────────────────────────────────────

export function cancelStream() {
  if (_abortController && _isStreaming) {
    _abortController.abort();
  }
}


// ── Strength ───────────────────────────────────────────────────────────────

/**
 * Sets the rewrite strength and updates the button UI.
 * @param {string} strength - 'light' | 'medium' | 'aggressive'
 * @param {HTMLElement} [activeBtn] - the button that was clicked
 */
export function setStrength(strength, activeBtn) {
  _currentStrength = strength;
  document.querySelectorAll('.strength-opt').forEach(b => {
    b.classList.toggle('active', b === activeBtn || b.dataset.strength === strength);
  });
}


// ── Copy output ────────────────────────────────────────────────────────────

export async function copyOutput() {
  if (!_lastOutput) {
    toast('Nothing to copy yet.', 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(_lastOutput);
    toast('Copied to clipboard!', 'success');
  } catch {
    // Clipboard API not available (non-HTTPS dev?) — fallback
    const ta = document.createElement('textarea');
    ta.value = _lastOutput;
    ta.style.position = 'fixed';
    ta.style.opacity  = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('Copied!', 'success');
  }
}


// ── Rate limit display ─────────────────────────────────────────────────────

/**
 * Fetches and displays the current rate limit usage in the nav bar.
 */
export async function loadUsageStats() {
  try {
    const response = await fetch(`${getBackendUrl()}/api/humanize/usage`, {
      headers: getAuthHeaders(),
    });
    if (!response.ok) return;

    const data = await response.json();
    _updateRateBadge(data.remaining_this_hour, data.limit_per_hour);
  } catch {
    // Non-critical — don't show an error for this
  }
}

function _updateRateBadge(remaining, limit) {
  const badge = document.getElementById('rateBadge');
  if (!badge) return;

  badge.textContent = `${remaining}/${limit} left`;
  badge.className   = 'rate-badge';

  if (remaining === 0)        badge.classList.add('empty');
  else if (remaining <= 5)    badge.classList.add('low');
}


// ── Private helpers ────────────────────────────────────────────────────────

function _escapeHTML(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Highlights words in the output that weren't in the input.
 * Words 5+ chars that are new get a green highlight — shows the
 * model's vocabulary substitutions visually.
 */
function _highlightChanges(original, rewritten) {
  const origWords = new Set(
    (original.toLowerCase().match(/\b\w+\b/g) || [])
  );
  return _escapeHTML(rewritten).replace(/\b(\w{5,})\b/g, word => {
    return origWords.has(word.toLowerCase())
      ? word
      : `<span class="diff-changed">${word}</span>`;
  });
}

function _renderOutput(input, output) {
  const outputEl = document.getElementById('outputContent');
  if (!outputEl) return;
  outputEl.classList.remove('empty');
  outputEl.innerHTML = _showDiff
    ? _highlightChanges(input, output)
    : _escapeHTML(output);
}

function _setOutputState(state, message) {
  const outputEl = document.getElementById('outputContent');
  if (!outputEl) return;

  if (state === 'empty') {
    outputEl.classList.add('empty');
    outputEl.textContent = 'Output will appear here after humanizing.';
  } else if (state === 'error') {
    outputEl.classList.remove('empty');
    outputEl.innerHTML = `<span style="color:var(--red)">${_escapeHTML(message || 'Unknown error')}</span>`;
  } else if (state === 'streaming') {
    outputEl.classList.remove('empty');
  }
}

function _showFeedbackRow(show) {
  const row = document.getElementById('feedbackRow');
  if (row) row.style.display = show ? 'flex' : 'none';
}

function _updateOutputWordCount(text) {
  const words = (text.match(/\S+/g) || []).length;
  const chars = text.length;
  const mins  = Math.max(1, Math.round(words / 200));

  const bar = document.getElementById('outputWCBar');
  if (bar) bar.style.display = 'flex';

  const wEl = document.getElementById('outputWords');
  const cEl = document.getElementById('outputChars');
  const rEl = document.getElementById('outputReadTime');

  if (wEl) wEl.textContent = `${words} words`;
  if (cEl) cEl.textContent = `${chars} chars`;
  if (rEl) rEl.textContent = `~${mins} min read`;
}

function _mapStatusToMessage(status, detail) {
  const map = {
    401: 'Session expired — please sign in again.',
    403: 'Access denied.',
    404: 'No API key found — add your Gemini key in Settings.',
    429: 'Rate limit reached — wait a minute and try again.',
    500: 'Server error — try again shortly.',
    503: 'Server unavailable — try again shortly.',
  };
  return map[status] || detail || `Request failed (HTTP ${status})`;
}
