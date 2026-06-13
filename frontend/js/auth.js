/**
 * auth.js
 *
 * Handles all authentication using Supabase's Google OAuth.
 *
 * Design decisions:
 * - The JWT (access token) is stored in memory only — never localStorage
 *   or sessionStorage. This prevents XSS attacks from stealing the token.
 *   The trade-off is that refreshing the page requires Supabase to silently
 *   re-issue a token, which it does automatically via a secure HttpOnly cookie.
 * - Supabase handles the OAuth redirect flow entirely. We just initialise
 *   the client, listen for auth state changes, and react accordingly.
 * - All backend API calls go through getAuthHeaders() which always reads
 *   the current in-memory token — never a cached or stale one.
 *
 * Usage (imported by index.html and app.html):
 *   import { signInWithGoogle, signOut, getAuthHeaders, getUser } from './auth.js'
 */

// ── Supabase client initialisation ────────────────────────────────────────
// These values are safe to expose in frontend code:
// - SUPABASE_URL is just a URL
// - SUPABASE_ANON_KEY is a public key — Row Level Security protects the data,
//   not key secrecy. Never put the SERVICE_KEY here.

const SUPABASE_URL      = 'https://kjorkefxzqypxczmgmld.supabase.co';      // replaced at deploy time
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtqb3JrZWZ4enF5cHhjem1nbWxkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEzNjc2NTgsImV4cCI6MjA5Njk0MzY1OH0.ieKWRRp5-rbp_09kfGuG_IqaPhTPD1FRZBbHR13lfn0'; // replaced at deploy time
const BACKEND_URL       = 'http://localhost:8000';        // e.g. https://humanizer.railway.app

// For local development, override these here:
// const SUPABASE_URL      = 'https://your-project.supabase.co';
// const SUPABASE_ANON_KEY = 'your-anon-key';
// const BACKEND_URL       = 'http://localhost:8000';

const { createClient } = supabase; // loaded via CDN script tag in HTML
const _supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ── In-memory session state ────────────────────────────────────────────────
// Never written to localStorage — lives only in this module's closure.

let _session  = null;  // Supabase Session object
let _user     = null;  // Supabase User object
let _onAuthChange = null; // callback registered by app code

// ── Auth state listener ────────────────────────────────────────────────────
// Supabase fires this on: initial page load, sign in, sign out, token refresh.
// This is the single place that drives all auth-dependent UI.

_supabase.auth.onAuthStateChange((event, session) => {
  _session = session;
  _user    = session?.user ?? null;

  console.log(`[auth] state change: ${event}`, _user?.email ?? 'signed out');

  if (_onAuthChange) {
    _onAuthChange(event, _user);
  }

  // ── Routing logic ──────────────────────────────────────────────
  const onLoginPage = window.location.pathname.endsWith('index.html')
    || window.location.pathname === '/'
    || window.location.pathname === '';

  const onAppPage = window.location.pathname.endsWith('app.html');

  if (event === 'SIGNED_IN' && onLoginPage) {
    // User just signed in — go to the app
    window.location.href = 'app.html';
    return;
  }

  if ((event === 'SIGNED_OUT' || !session) && onAppPage) {
    // Session gone — back to login
    window.location.href = 'index.html';
    return;
  }

  if (event === 'TOKEN_REFRESHED') {
    // Token silently refreshed — nothing visible to the user needed
    console.log('[auth] token refreshed silently');
  }
});


// ── Public API ─────────────────────────────────────────────────────────────

/**
 * Trigger Google OAuth sign-in via Supabase.
 * Redirects to Google, then back to the redirect URL below.
 * Supabase handles the code exchange and issues a session automatically.
 */
export async function signInWithGoogle() {
  const { error } = await _supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: `${window.location.origin}/app.html`,
      queryParams: {
        // Request offline access so Supabase can refresh tokens
        access_type: 'offline',
        prompt: 'select_account', // Always show account picker
      },
    },
  });

  if (error) {
    console.error('[auth] signInWithGoogle error:', error);
    throw error;
  }
}


/**
 * Sign the user out and redirect to the login page.
 * Clears the Supabase session cookie and our in-memory state.
 */
export async function signOut() {
  const { error } = await _supabase.auth.signOut();
  _session = null;
  _user    = null;

  if (error) {
    console.error('[auth] signOut error:', error);
    // Redirect anyway — better to land on login than stay on a broken state
  }

  window.location.href = 'index.html';
}


/**
 * Returns the current user object, or null if not signed in.
 * @returns {Object|null} Supabase user object
 */
export function getUser() {
  return _user;
}


/**
 * Returns the current access token (JWT) for backend API calls.
 * Reads from in-memory state — never localStorage.
 * @returns {string|null}
 */
export function getAccessToken() {
  return _session?.access_token ?? null;
}


/**
 * Returns Authorization headers for fetch() calls to the backend.
 * Use this in every API call — never hardcode the token.
 *
 * @returns {Object} Headers object with Authorization and Content-Type
 *
 * @example
 * const response = await fetch(`${BACKEND_URL}/api/profile`, {
 *   headers: getAuthHeaders(),
 * });
 */
export function getAuthHeaders() {
  const token = getAccessToken();
  if (!token) {
    throw new Error('No active session. Please sign in.');
  }
  return {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}


/**
 * Returns the backend base URL.
 * All API calls should use this rather than hardcoding the URL.
 * @returns {string}
 */
export function getBackendUrl() {
  return BACKEND_URL;
}


/**
 * Registers a callback to be called whenever auth state changes.
 * The app uses this to update the UI (show user email, update nav, etc.)
 *
 * @param {Function} callback - fn(event: string, user: Object|null)
 *
 * @example
 * onAuthChange((event, user) => {
 *   if (user) showUserEmail(user.email);
 *   else showSignedOut();
 * });
 */
export function onAuthChange(callback) {
  _onAuthChange = callback;
}


/**
 * Checks whether the backend is reachable.
 * Called on app startup to show a connection warning if the backend is down.
 * @returns {Promise<boolean>}
 */
export async function checkBackendHealth() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { method: 'GET' });
    return response.ok;
  } catch {
    return false;
  }
}


/**
 * Verifies the current session token is still accepted by the backend.
 * Called after page load to catch expired tokens before the user tries
 * to do something and gets a confusing 401.
 * @returns {Promise<boolean>}
 */
export async function verifySession() {
  try {
    const response = await fetch(`${BACKEND_URL}/api/auth/verify`, {
      headers: getAuthHeaders(),
    });
    return response.ok;
  } catch {
    return false;
  }
}


/**
 * Initialises auth on page load.
 * Restores the session from Supabase's secure cookie (if one exists),
 * which avoids forcing the user to re-login on every page refresh.
 *
 * Call this once at the top of each page's script.
 *
 * @returns {Promise<Object|null>} The current user, or null
 */
export async function initAuth() {
  const { data: { session }, error } = await _supabase.auth.getSession();

  if (error) {
    console.error('[auth] getSession error:', error);
    return null;
  }

  if (session) {
    _session = session;
    _user    = session.user;
    console.log('[auth] restored session for:', _user.email);
  }

  return _user;
}
