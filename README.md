# Humaniser

A self-hosted web application that rewrites AI-generated text to match your personal writing style. You supply a Gemini API key; the application encrypts and stores it server-side, proxies all API calls through a Python backend, and never exposes your key to the browser.

---

## Overview

Humaniser analyses samples of your own writing to build a statistical style profile — sentence length, vocabulary preferences, punctuation habits, formality level — then uses that profile to instruct Gemini to rewrite AI text so it reads like you wrote it. The profile is built and refined entirely client-side (no external ML service) and stored in Supabase so it persists across devices and sessions.

Authentication is handled by Supabase Google OAuth. All protected routes require a valid JWT issued by Supabase. The backend validates every token signature before processing any request.

---

## Architecture

```
frontend/               Static HTML + Vanilla JS (no build step)
  index.html            Login page (Google OAuth entry point)
  app.html              Main application shell
  css/                  Stylesheet
  js/
    auth.js             Supabase OAuth flow, in-memory token management
    humanize.js         Text input, streaming SSE reader, output rendering
    profile.js          Style profile analysis and feedback loop
    history.js          Rewrite history (session storage)
    ui.js               Shared UI helpers and toast notifications

backend/                FastAPI application (Python 3.11+)
  main.py               App factory, CORS, startup checks
  core/
    config.py           Environment variable loading and validation
    encryption.py       AES-256-GCM key encryption/decryption
    rate_limiter.py     Per-user hourly request counter (Supabase-backed)
    prompt_builder.py   System prompt construction from style profile
  routers/
    auth.py             JWT validation dependency (get_current_user)
    humanize.py         POST /api/humanize, key management endpoints
    profile.py          Style profile CRUD
  models/
    schemas.py          Pydantic request/response models

eval/
  benchmark.py          Offline benchmark runner for prompt quality testing
  test_cases.json       Curated test inputs with expected characteristics
```

---

## Features

- **Style-matched rewriting.** Builds a profile from your own text samples covering average sentence length, vocabulary richness, punctuation density, formality score, and more. Humanize strength is adjustable (Subtle, Balanced, Heavy).
- **Streaming output.** The Gemini response is forwarded to the browser as Server-Sent Events, so text appears progressively rather than after a full round-trip.
- **Encrypted key storage.** Your Gemini API key is encrypted with AES-256-GCM before being stored in Supabase. The plaintext key never appears in the database or in any log.
- **Per-user rate limiting.** Each account is capped at 20 rewrites per hour. Usage is tracked in Supabase and exposed to the frontend via `GET /api/humanize/usage`.
- **Feedback loop.** Clicking "Accept and learn" on a rewrite merges the accepted output back into your style profile, improving future rewrites over time.
- **Zero-exposure API key design.** The Gemini API key is fetched from Supabase, decrypted, used for a single request, and discarded in memory. It is never sent to the frontend.

---

## Requirements

- Python 3.11 or later
- A Supabase project (free tier is sufficient)
- A Google Gemini API key (obtainable at aistudio.google.com)
- Google OAuth configured in your Supabase project

---

## Supabase Setup

Create the following tables in your Supabase project.

**profiles**

| Column       | Type      | Notes                        |
|--------------|-----------|------------------------------|
| user_id      | uuid      | Primary key, references auth.users |
| profile_data | jsonb     | Serialised StyleProfile object |
| updated_at   | timestamptz |                            |

**api_keys**

| Column        | Type | Notes                              |
|---------------|------|------------------------------------|
| user_id       | uuid | Primary key, references auth.users |
| encrypted_key | text | AES-256-GCM ciphertext (hex)       |

Enable Row Level Security on both tables and add policies so users can only read and write their own rows.

Enable Google as an OAuth provider under Authentication > Providers. Add `http://localhost:3000` (and your production URL) to the Redirect URLs list.

---

## Local Setup

**1. Clone the repository**

```bash
git clone <repository-url>
cd Humaniser
```

**2. Configure environment variables**

```bash
cp .env.example .env
```

Edit `.env` and fill in all required values:

```
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key
SUPABASE_JWT_SECRET=your-jwt-secret
ENCRYPTION_SECRET=your-64-char-hex-string
ALLOWED_ORIGINS=http://localhost:3000
PORT=8000
ENVIRONMENT=development
```

Generate the encryption secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The `SUPABASE_JWT_SECRET` is found at Supabase dashboard > Project Settings > API > JWT Secret. It is only used as a fallback for HS256-signed tokens; the primary validation path uses your project's JWKS endpoint.

**3. Create and activate a virtual environment**

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

**4. Install dependencies**

```bash
pip install -r requirements.txt
```

**5. Start the backend**

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`. Interactive API documentation is available at `http://localhost:8000/docs` in development mode.

**6. Serve the frontend**

The frontend is plain HTML and JavaScript with no build step. Serve it from any static file server on port 3000, for example:

```bash
cd frontend
python -m http.server 3000
```

Open `http://localhost:3000` in your browser.

---

## API Reference

All endpoints except `/health` and `/` require `Authorization: Bearer <token>` in the request headers, where the token is the Supabase access token obtained after Google OAuth sign-in.

### Authentication

| Method | Path              | Description                                      |
|--------|-------------------|--------------------------------------------------|
| GET    | /api/auth/me      | Returns the authenticated user's ID and email    |
| GET    | /api/auth/verify  | Lightweight token validity check                 |

### Humanize

| Method | Path                | Description                                          |
|--------|---------------------|------------------------------------------------------|
| POST   | /api/humanize       | Rewrite text. Returns a streaming SSE response       |
| GET    | /api/humanize/usage | Current hourly usage count and remaining requests    |

Request body for `POST /api/humanize`:

```json
{
  "text": "The text to rewrite.",
  "strength": "balanced",
  "profile": { ... }
}
```

`strength` accepts `subtle`, `balanced`, or `heavy`.

### API Key Management

| Method | Path            | Description                                              |
|--------|-----------------|----------------------------------------------------------|
| POST   | /api/key        | Encrypt and store a Gemini API key                       |
| GET    | /api/key/status | Check whether a key is stored (does not return the key) |
| DELETE | /api/key        | Remove the stored API key                                |

### Style Profile

| Method | Path                  | Description                                             |
|--------|-----------------------|---------------------------------------------------------|
| GET    | /api/profile          | Load the user's stored style profile                    |
| POST   | /api/profile          | Save or update the style profile                        |
| DELETE | /api/profile          | Reset the profile to default (generic humanization)     |
| POST   | /api/profile/sample   | Merge an accepted rewrite output into the profile       |

### Health

| Method | Path    | Description                          |
|--------|---------|--------------------------------------|
| GET    | /health | Returns `{"status": "ok"}`. No auth required. |

---

## JWT Validation

The backend validates Supabase JWTs in `routers/auth.py`. It first attempts HS256 verification using the JWT secret from the environment. If that fails, it falls back to fetching the JWKS from `https://<project>.supabase.co/auth/v1/.well-known/jwks.json` and verifies against RS256 or ES256 depending on which algorithm your Supabase project uses. Modern Supabase projects use ES256; both are supported.

---

## Encryption

Gemini API keys are encrypted using AES-256-GCM (`cryptography` library) before storage. Each encryption operation generates a random 12-byte nonce. The ciphertext, nonce, and authentication tag are stored together as a hex-encoded string. Decryption fails loudly if the ciphertext has been tampered with.

The `ENCRYPTION_SECRET` environment variable must be a 64-character hex string (32 bytes). Rotating this key will invalidate all stored encrypted keys — users would need to re-enter their Gemini keys.

---

## Rate Limiting

Requests are counted per user per calendar hour in Supabase. The current limit is 20 rewrites per hour. The remaining count is returned in the `X-RateLimit-Limit-Hour` response header and via `GET /api/humanize/usage`. The counter resets automatically at the start of each new hour.

---

## Evaluation

The `eval/` directory contains an offline benchmark runner for testing prompt quality against a set of curated inputs.

```bash
cd eval
python benchmark.py
```

This requires `GEMINI_API_KEY` to be set in the environment. It does not require a running backend.

---

## Deployment

The backend is designed for deployment on Railway or Render. Set all environment variables in the platform's dashboard and use the following start command:

```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

The frontend can be deployed to any static host (GitHub Pages, Netlify, Vercel). Update `BACKEND_URL` in `frontend/js/auth.js` to point to your deployed backend URL and update `ALLOWED_ORIGINS` in the backend environment to include your frontend's origin.

---

## Security Notes

- The `SUPABASE_SERVICE_KEY` bypasses Row Level Security. It is only used server-side and must never be sent to the frontend.
- API documentation (`/docs`, `/redoc`) is disabled automatically when `ENVIRONMENT=production`.
- The `ENCRYPTION_SECRET` must be kept out of version control. The `.gitignore` excludes `.env` by default.
- JWT tokens are stored in memory in the browser and are never written to `localStorage` or `sessionStorage`, reducing exposure to XSS attacks.

---

## License

Private project. Not licensed for redistribution.
