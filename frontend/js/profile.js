/**
 * profile.js
 *
 * Everything to do with the user's writing style profile:
 * - Statistical feature extraction from raw text samples
 * - Local state management (samples array, built profile)
 * - Syncing the profile to/from the backend
 * - Feedback loop: merging accepted rewrite outputs into the profile
 * - Rendering the profile UI (stats, badges, detail cards)
 *
 * The feature extraction runs entirely in the browser — no API call needed.
 * It's pure JavaScript statistics on the text, which keeps latency at zero
 * and means the profile works even if the backend is temporarily down.
 *
 * Exports used by app.html inline script and other JS modules:
 *   extractFeatures, addSample, buildProfile, clearSamples,
 *   getProfile, acceptOutput, saveProfileToBackend, loadProfileFromBackend,
 *   renderProfileStats, renderProfileDetail
 */

import { getAuthHeaders, getBackendUrl } from './auth.js';

// ── Module state ───────────────────────────────────────────────────────────

let _samples          = [];   // raw text strings the user has pasted
let _acceptedOutputs  = [];   // rewrite outputs the user has accepted
let _profile          = null; // the built StyleProfile object, or null

// AI filler words — detected in samples and flagged as warnings
const AI_FILLERS = [
  'furthermore', 'additionally', 'notably', 'crucial', 'delve',
  'moreover', 'thus', 'hence', 'nevertheless', 'notwithstanding',
  'importantly', 'significantly', 'in conclusion', 'to summarize',
  'in summary', 'it is worth noting', 'it is important',
  'groundbreaking', 'paradigm', 'leverage', 'utilize', 'robust',
  'seamless', 'revolutionize', 'streamline', 'cutting-edge',
];

// Natural hedge phrases humans use — positive signal
const HUMAN_HEDGES = [
  'maybe', 'honestly', 'i think', 'kind of', 'sort of', 'anyway',
  'look', 'i mean', 'basically', 'literally', 'actually',
  'tbh', 'ngl', 'low-key', 'i guess', 'to be fair', 'to be honest',
];

// Common stopwords — excluded from distinctive vocabulary detection
const STOPWORDS = new Set([
  'about', 'which', 'their', 'there', 'these', 'those', 'where',
  'would', 'could', 'should', 'after', 'before', 'while', 'being',
  'every', 'since', 'until', 'other', 'under', 'again', 'further',
  'then', 'once', 'just', 'more', 'also', 'have', 'this', 'that',
  'with', 'from', 'they', 'been', 'were', 'will', 'into', 'than',
]);


// ── Core feature extraction ────────────────────────────────────────────────

/**
 * Extracts statistical style features from an array of text strings.
 * This is the NLP feature engineering layer — the key technical piece
 * that justifies the "more than a wrapper" claim in the README.
 *
 * @param {string[]} texts - Array of writing sample strings
 * @returns {Object} StyleProfile-shaped object matching backend schema
 */
export function extractFeatures(texts) {
  const allText   = texts.join('\n');
  const sentences = allText
    .split(/[.!?]+/)
    .map(s => s.trim())
    .filter(s => s.length > 4 && s.split(/\s+/).length > 1);

  const words = (allText.toLowerCase().match(/\b[a-z']+\b/g) || []);

  if (sentences.length === 0 || words.length === 0) {
    return _defaultProfile(texts.length);
  }

  // ── Sentence length stats ──────────────────────────────────────
  const lengths  = sentences.map(s => s.split(/\s+/).length);
  const avgLen   = lengths.reduce((a, b) => a + b, 0) / lengths.length;
  const variance = lengths.reduce((a, b) => a + Math.pow(b - avgLen, 2), 0) / lengths.length;
  const burstiness = Math.sqrt(variance);

  // ── Contraction rate ───────────────────────────────────────────
  // e.g. don't, I'm, we'll — signals casual register
  const contractionCount = words.filter(w => w.includes("'")).length;
  const contractionRate  = contractionCount / words.length;

  // ── First-person rate ──────────────────────────────────────────
  const FP_WORDS = new Set(["i", "me", "my", "mine", "myself", "i'm", "i've", "i'll", "i'd"]);
  const fpCount  = words.filter(w => FP_WORDS.has(w)).length;
  const firstPersonRate = fpCount / words.length;

  // ── Fragment rate ──────────────────────────────────────────────
  // Sentences under 5 words — common in natural speech
  const fragmentRate = lengths.filter(l => l < 5).length / lengths.length;

  // ── Exclamation rate ───────────────────────────────────────────
  const exclamationCount = (allText.match(/!/g) || []).length;
  const exclamationRate  = exclamationCount / sentences.length;

  // ── Average word length ────────────────────────────────────────
  // Short = Anglo-Saxon / direct; Long = Latinate / formal
  const avgWordLength = words.reduce((a, w) => a + w.length, 0) / words.length;

  // ── Punctuation habits ─────────────────────────────────────────
  const usesDash      = allText.includes('—') || / - /.test(allText);
  const usesEllipsis  = allText.includes('...');
  const usesSemicolon = allText.includes(';');

  // ── Vocabulary: distinctive words ─────────────────────────────
  // Words appearing 2–6 times across all samples — characteristic
  // but not so common they're just stopwords
  const freq = {};
  words.forEach(w => {
    if (w.length > 4 && !STOPWORDS.has(w)) {
      freq[w] = (freq[w] || 0) + 1;
    }
  });
  const distinctiveWords = Object.entries(freq)
    .filter(([, c]) => c >= 2 && c <= 6)
    .sort((a, b) => b[1] - a[1])
    .map(([w]) => w)
    .slice(0, 20);

  // ── AI filler detection ────────────────────────────────────────
  const lowerText   = allText.toLowerCase();
  const foundFillers = AI_FILLERS.filter(f => lowerText.includes(f));

  // ── Human hedge detection ──────────────────────────────────────
  const foundHedges = HUMAN_HEDGES.filter(h => lowerText.includes(h));

  return {
    avg_sentence_length: Math.round(avgLen * 10) / 10,
    burstiness:          Math.round(burstiness * 10) / 10,
    fragment_rate:       Math.round(fragmentRate * 1000) / 1000,
    contraction_rate:    Math.round(contractionRate * 1000) / 1000,
    first_person_rate:   Math.round(firstPersonRate * 1000) / 1000,
    avg_word_length:     Math.round(avgWordLength * 10) / 10,
    exclamation_rate:    Math.round(exclamationRate * 100) / 100,
    uses_dash:           usesDash,
    uses_ellipsis:       usesEllipsis,
    uses_semicolon:      usesSemicolon,
    distinctive_words:   distinctiveWords,
    found_hedges:        foundHedges,
    found_fillers:       foundFillers,
    sample_count:        texts.length,
    word_count:          words.length,
  };
}

function _defaultProfile(sampleCount = 0) {
  return {
    avg_sentence_length: 15.0,
    burstiness:          5.0,
    fragment_rate:       0.05,
    contraction_rate:    0.03,
    first_person_rate:   0.02,
    avg_word_length:     4.5,
    exclamation_rate:    0.02,
    uses_dash:           false,
    uses_ellipsis:       false,
    uses_semicolon:      false,
    distinctive_words:   [],
    found_hedges:        [],
    found_fillers:       [],
    sample_count:        sampleCount,
    word_count:          0,
  };
}


// ── Sample management ──────────────────────────────────────────────────────

/**
 * Adds a writing sample to the local collection.
 * @param {string} text - Raw writing sample text
 * @returns {{ success: boolean, message: string }}
 */
export function addSample(text) {
  const cleaned = text.trim();
  if (!cleaned || cleaned.length < 30) {
    return { success: false, message: 'Sample too short — paste at least a few sentences.' };
  }
  _samples.push(cleaned);
  return { success: true, message: `Sample added (${_samples.length} total)` };
}

/**
 * Clears all samples and resets the profile.
 */
export function clearSamples() {
  _samples         = [];
  _acceptedOutputs = [];
  _profile         = null;
}

/**
 * Returns the number of samples currently loaded.
 * @returns {number}
 */
export function getSampleCount() {
  return _samples.length;
}

/**
 * Builds the style profile from current samples + accepted outputs.
 * Must be called before the profile can be used for humanization.
 * @returns {Object|null} The built profile, or null if no samples
 */
export function buildProfile() {
  if (_samples.length === 0) return null;
  const allTexts = [..._samples, ..._acceptedOutputs];
  _profile = extractFeatures(allTexts);
  return _profile;
}

/**
 * Returns the current built profile, or null if not yet built.
 * @returns {Object|null}
 */
export function getProfile() {
  return _profile;
}


// ── Feedback loop ──────────────────────────────────────────────────────────

/**
 * Accepts a humanized output and folds it into the profile.
 * The accepted text becomes a new training sample, shifting the
 * profile toward the style of approved rewrites over time.
 *
 * @param {string} outputText - The accepted rewrite output
 * @returns {Object} The updated profile
 */
export function acceptOutput(outputText) {
  _acceptedOutputs.push(outputText.trim());
  const allTexts = [..._samples, ..._acceptedOutputs];
  _profile = extractFeatures(allTexts);
  return _profile;
}

/**
 * Returns the number of accepted outputs incorporated into the profile.
 * @returns {number}
 */
export function getAcceptedCount() {
  return _acceptedOutputs.length;
}


// ── Backend sync ───────────────────────────────────────────────────────────

/**
 * Saves the current profile to the backend.
 * Called after buildProfile() or acceptOutput().
 *
 * @returns {Promise<boolean>} true if saved successfully
 */
export async function saveProfileToBackend() {
  if (!_profile) return false;

  try {
    const response = await fetch(`${getBackendUrl()}/api/profile`, {
      method:  'POST',
      headers: getAuthHeaders(),
      body:    JSON.stringify({ profile: _profile }),
    });
    return response.ok;
  } catch (err) {
    console.error('[profile] saveProfileToBackend error:', err);
    return false;
  }
}

/**
 * Loads the user's profile from the backend.
 * Called on app startup to restore the profile across sessions/devices.
 *
 * @returns {Promise<Object|null>} The loaded profile, or null
 */
export async function loadProfileFromBackend() {
  try {
    const response = await fetch(`${getBackendUrl()}/api/profile`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) return null;

    const data = await response.json();
    if (data.exists && data.profile) {
      _profile = data.profile;
      return _profile;
    }
    return null;
  } catch (err) {
    console.error('[profile] loadProfileFromBackend error:', err);
    return null;
  }
}

/**
 * Sends the updated profile to the backend after accepting an output.
 * Uses the /api/profile/sample endpoint to signal this came from feedback.
 *
 * @returns {Promise<boolean>}
 */
export async function saveAcceptedSampleToBackend() {
  if (!_profile) return false;

  try {
    const response = await fetch(`${getBackendUrl()}/api/profile/sample`, {
      method:  'POST',
      headers: getAuthHeaders(),
      body:    JSON.stringify({ profile: _profile }),
    });
    return response.ok;
  } catch (err) {
    console.error('[profile] saveAcceptedSampleToBackend error:', err);
    return false;
  }
}


// ── UI rendering ───────────────────────────────────────────────────────────

/**
 * Renders the compact stats strip shown on the Setup tab after adding samples.
 * Updates the three stat boxes and the badge row.
 *
 * @param {Object} profile - StyleProfile object (from extractFeatures or backend)
 */
export function renderProfileStats(profile) {
  const statsEl  = document.getElementById('profileStats');
  const badgesEl = document.getElementById('profileBadges');
  if (!statsEl || !badgesEl) return;

  if (!profile || profile.sample_count === 0) {
    statsEl.style.display  = 'none';
    badgesEl.innerHTML     = '';
    return;
  }

  statsEl.style.display = 'grid';

  _setText('statBurst',        profile.burstiness.toFixed(1));
  _setText('statSentLen',      profile.avg_sentence_length.toFixed(0) + 'w');
  _setText('statContractions', (profile.contraction_rate * 100).toFixed(1) + '%');

  // Build badge list
  const badges = [];
  if (profile.uses_dash)              badges.push({ t: 'em dash',          cls: 'badge-green' });
  if (profile.uses_ellipsis)          badges.push({ t: 'ellipsis',         cls: 'badge-green' });
  if (profile.uses_semicolon)         badges.push({ t: 'semicolons',       cls: 'badge-green' });
  if (profile.fragment_rate > 0.05)   badges.push({ t: 'uses fragments',   cls: 'badge-green' });
  if (profile.first_person_rate > 0.01) badges.push({ t: 'first-person',  cls: 'badge-green' });
  if (profile.found_hedges.length > 0)  badges.push({ t: 'natural hedges', cls: 'badge-green' });
  if (profile.found_fillers.length > 0) badges.push({ t: '⚠ AI fillers found', cls: 'badge-red' });
  if (profile.burstiness < 4)           badges.push({ t: '⚠ low burstiness',   cls: 'badge-red' });

  badgesEl.innerHTML = badges
    .map(b => `<span class="badge ${b.cls}">${b.t}</span>`)
    .join('');
}

/**
 * Renders the full profile detail view on the Profile tab.
 * Shows all metrics with meters, distinctive vocabulary, and hedge/filler lists.
 *
 * @param {Object} profile - StyleProfile object
 * @param {number} acceptedCount - Number of accepted outputs in profile
 */
export function renderProfileDetail(profile, acceptedCount = 0) {
  const emptyEl  = document.getElementById('profileEmpty');
  const detailEl = document.getElementById('profileDetail');
  if (!emptyEl || !detailEl) return;

  if (!profile) {
    emptyEl.style.display  = 'block';
    detailEl.style.display = 'none';
    return;
  }

  emptyEl.style.display  = 'none';
  detailEl.style.display = 'grid';

  const cards = [
    {
      label: 'Burstiness',
      value: profile.burstiness.toFixed(1),
      desc:  profile.burstiness > 8
        ? 'High variation — human-like rhythm.'
        : 'Low variation — introduce more sentence length variety.',
      meter: Math.min(profile.burstiness / 15, 1),
      meterClass: profile.burstiness > 8 ? '' : 'amber',
    },
    {
      label: 'Avg sentence length',
      value: profile.avg_sentence_length + 'w',
      desc:  'Words per sentence on average.',
      meter: Math.min(profile.avg_sentence_length / 30, 1),
    },
    {
      label: 'Contraction rate',
      value: (profile.contraction_rate * 100).toFixed(1) + '%',
      desc:  profile.contraction_rate > 0.04
        ? 'Casual — contractions used naturally.'
        : 'Formal — contractions mostly avoided.',
      meter: Math.min(profile.contraction_rate / 0.15, 1),
    },
    {
      label: 'First-person rate',
      value: (profile.first_person_rate * 100).toFixed(1) + '%',
      desc:  profile.first_person_rate > 0.015
        ? 'Personal voice — I / me / my used.'
        : 'Impersonal — third-person style.',
      meter: Math.min(profile.first_person_rate / 0.06, 1),
    },
    {
      label: 'Fragment rate',
      value: (profile.fragment_rate * 100).toFixed(0) + '%',
      desc:  'Short sentences under 5 words.',
      meter: Math.min(profile.fragment_rate / 0.2, 1),
    },
    {
      label: 'Avg word length',
      value: profile.avg_word_length.toFixed(1),
      desc:  profile.avg_word_length > 5
        ? 'Latinate — longer, formal words.'
        : 'Direct — short Anglo-Saxon words.',
      meter: Math.min((profile.avg_word_length - 3) / 4, 1),
    },
  ];

  detailEl.innerHTML = cards.map(c => `
    <div class="detail-card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      <div class="desc">${c.desc}</div>
      <div class="meter">
        <div class="meter-fill ${c.meterClass || ''}"
             style="width:${Math.round((c.meter || 0) * 100)}%">
        </div>
      </div>
    </div>
  `).join('')

  + _sectionCard('Distinctive vocabulary',
      profile.distinctive_words.map(w =>
        `<span class="word-chip">${w}</span>`
      ).join('') || '<span class="text-dim text-xs">None detected yet</span>',
      'grid-column: 1 / -1'
    )

  + (profile.found_hedges.length > 0 ? _sectionCard(
      'Natural hedges detected',
      profile.found_hedges.map(w =>
        `<span class="word-chip" style="border-color:var(--green-mid);color:var(--green-text)">${w}</span>`
      ).join(''),
      'grid-column: 1 / -1'
    ) : '')

  + (profile.found_fillers.length > 0 ? _sectionCard(
      '<span style="color:var(--red)">AI fillers found in samples ⚠</span>',
      profile.found_fillers.map(w =>
        `<span class="word-chip" style="border-color:var(--red-mid);color:var(--red)">${w}</span>`
      ).join('')
      + `<p class="text-xs text-dim mt-2" style="font-family:var(--font-sans);width:100%">
           Found in your writing samples — they will be excluded from humanized output.
         </p>`,
      'grid-column: 1 / -1'
    ) : '')

  + `
    <div class="detail-card">
      <div class="label">Punctuation style</div>
      <div class="word-cloud" style="margin-top:var(--sp-2)">
        ${profile.uses_dash     ? '<span class="word-chip" style="color:var(--green-text)">em dash</span>'  : ''}
        ${profile.uses_ellipsis ? '<span class="word-chip" style="color:var(--green-text)">ellipsis</span>' : ''}
        ${profile.uses_semicolon? '<span class="word-chip" style="color:var(--green-text)">semicolons</span>' : ''}
        ${!profile.uses_dash && !profile.uses_ellipsis && !profile.uses_semicolon
          ? '<span class="text-dim text-xs">Standard punctuation only</span>' : ''}
      </div>
    </div>

    <div class="detail-card">
      <div class="label">Training data</div>
      <div class="value">${profile.sample_count}</div>
      <div class="desc">${(profile.word_count || 0).toLocaleString()} words total</div>
      ${acceptedCount > 0
        ? `<div class="text-xs mt-2" style="color:var(--green-text)">
             + ${acceptedCount} accepted output(s) included
           </div>`
        : ''}
    </div>
  `;
}


// ── Private helpers ────────────────────────────────────────────────────────

function _setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function _sectionCard(label, contentHTML, style = '') {
  return `
    <div class="detail-card" style="${style}">
      <div class="label">${label}</div>
      <div class="word-cloud">${contentHTML}</div>
    </div>`;
}
