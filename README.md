# Job Application Tracker (Solo MVP)

Reads your Gmail (read-only) for job-application-related emails, sorts them into a kanban board — Applied, Interview/Assessment, Offer, Rejected — using simple keyword matching. No account system yet: this version is built to run against your own inbox first. See "Going multi-user" at the bottom for what changes when you're ready to add other people.

## How it classifies emails

No ML, no external API calls — just keyword matching against the subject and body (see `backend/classifier.py`). It checks for rejection phrases first (so "we enjoyed interviewing you, but unfortunately..." still gets marked rejected), then offer language, then interview/assessment language, then generic "application received" confirmations. Anything that matches none of those, but still looks job-related (from an ATS domain like Greenhouse/Lever/Workday, or contains words like "application"/"interview"/"recruiter"), lands in "Other" so you can sort it manually. You can always correct a card's status or company name directly in the UI — once you do, future syncs won't overwrite your correction.

## 1. Set up Gmail API access (one-time, ~10 minutes)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (any name).
2. Go to **APIs & Services → Library**, search for "Gmail API", and enable it.
3. Go to **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - Fill in an app name (e.g. "Job Tracker"), your email as support/developer contact.
   - Scopes: you can skip adding scopes here — the app requests `gmail.readonly` directly in code.
   - Under **Test users**, add your own Gmail address. This keeps the app in "Testing" mode, which skips Google's verification process entirely for personal use.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Name it anything.
   - Click **Download JSON** on the credential you just created.
5. Rename that downloaded file to `credentials.json` and put it in the `backend/` folder. (It's already in `.gitignore` — never commit it.)

## 2. Backend setup

The database is Postgres (a free hosted instance works fine - see "Deploying it" below for how to get one from Supabase or Neon). Copy `backend/.env.example` to `backend/.env` and set `DATABASE_URL` before starting the backend, even for local dev.

```bash
cd backend
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

python authorize.py
```

`authorize.py` opens your browser, asks you to sign in and approve read-only Gmail access, and saves the result to `backend/token.json`. You'll see an "unverified app" warning from Google — that's expected and fine, since you're the developer; click "Continue" through it.

Then start the API:

```bash
python app.py
```

Runs on `http://localhost:5001`. Check `http://localhost:5001/api/health` — `tokenPresent` should say `true`.

**Note on re-authorizing:** because the app is in Google's "Testing" publishing status (the setting that avoids needing full verification), Google expires your access after about 7 days. When that happens, `/api/sync` will start returning an auth error — just re-run `python authorize.py` to reconnect. See the README section below if you outgrow this and want longer-lived access.

## 3. Frontend setup

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the printed URL (usually `http://localhost:5173`). Click **Sync now** to pull in your emails. Click any card's company name to edit it inline; use the dropdown on a card to correct its status.

## Customizing what gets searched

By default it searches Gmail for anything matching common application/interview language or known ATS domains, from the last 180 days (see `DEFAULT_SEARCH_QUERY` in `backend/app.py`). You can override this with your own [Gmail search syntax](https://support.google.com/mail/answer/7190) by setting `GMAIL_SEARCH_QUERY` in `backend/.env` (copy `.env.example` to `.env` first).

## Deploying it (auto-sync every 30 minutes)

Running locally, syncing only happens when you click "Sync now" with your laptop on. To have it check your inbox automatically every 30 minutes even when your computer is off, three things need to leave your machine: the database, the backend, and an external clock to trigger syncs on a schedule. The frontend can optionally leave too, so the whole thing is usable from your phone.

**1. Free hosted Postgres.** Create a free project on [Supabase](https://supabase.com) or [Neon](https://neon.tech) and copy its connection string. Use this same `DATABASE_URL` locally and on the deployed backend, so both read/write the same data.

**2. Publish the OAuth consent screen (important, do this before extracting a refresh token).** While your Google Cloud project is in "Testing" status, refresh tokens expire after 7 days - fine when you're the one re-running `python authorize.py`, but it would silently break the deployed auto-sync every week with nobody there to notice. In [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → OAuth consent screen**, click **Publish App** to move it to "In production." You'll see an "unverified app" warning the next time you authorize - that's expected for a personal app with one user; click through it. This doesn't require Google's full verification process since you're the only user.

**3. Get a long-lived refresh token.** Re-run `python authorize.py` locally (delete the old `token.json` first) now that the app is published, so the new token doesn't carry the 7-day expiry. Then pull three values out of the resulting files:
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` - from `backend/credentials.json`, under `"installed"`.
   - `GOOGLE_REFRESH_TOKEN` - from `backend/token.json`, the `"refresh_token"` field.

**4. Generate a shared secret.** Once deployed, `/api/*` is reachable by anyone on the internet and serves data pulled from your Gmail, so it needs to be locked down:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   This becomes `API_SECRET` on the backend.

**5. Deploy the backend (Render).** Create a new Web Service from this repo, root directory `backend/`. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app` (or leave it - the included `Procfile` covers it). Set these environment variables:
   - `DATABASE_URL` (from step 1)
   - `GROQ_API_KEY` (if you're using AI summaries)
   - `GOOGLE_REFRESH_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (from step 3)
   - `API_SECRET` (from step 4)
   - `ALLOWED_ORIGIN` - your Vercel URL from step 6 (you can add this after that step deploys and you have the URL)

   Note Render's free web service tier spins down after inactivity and wakes on the next request (with a delay) - the GitHub Actions cron in step 7 pinging it every 30 minutes keeps it warm as a side effect.

**6. Deploy the frontend (Vercel).** Import this repo, root directory `frontend/`. Set environment variables:
   - `VITE_API_BASE_URL` - your Render backend's URL, no trailing slash
   - `VITE_API_SECRET` - same value as `API_SECRET` above

   Once it's deployed, go back and set `ALLOWED_ORIGIN` on Render to this Vercel URL, then redeploy the backend so CORS picks it up.

**7. Schedule the sync (GitHub Actions).** This repo already includes `.github/workflows/sync.yml`, which POSTs to `/api/sync` every 30 minutes. In your GitHub repo's **Settings → Secrets and variables → Actions**, add:
   - `SYNC_URL` - your Render backend's URL, no trailing slash
   - `SYNC_API_SECRET` - same value as `API_SECRET`

   GitHub may delay scheduled workflow runs slightly under load, and disables them automatically if the repo goes 60 days without any activity - not a concern as long as you're still using the app, but worth knowing if syncing seems to have silently stopped after a long break.

## Going multi-user (15-20+ people)

This version deliberately keeps auth simple (one local `token.json`, one SQLite file) since it's built for your own inbox first. To open it up to other people, the pieces that need to change are:

- **Auth**: swap the `authorize.py` script for a real web-based OAuth flow in Flask (a `/login` route redirecting to Google, a `/callback` route exchanging the code for tokens), with each user's tokens stored per-account instead of one shared `token.json`.
- **Storage**: add a `user_id` column to the `applications` table (or move to Postgres, matching your LineTracker setup) so each person only sees their own data.
- **Test users**: add each person's Gmail address as a test user in the Google Cloud OAuth consent screen (up to 100 allowed without Google's full verification).
- **The 7-day token expiry**: still applies per-person in Testing mode. Either have people reconnect periodically, or apply for Google's "personal use" exception when publishing to Production (for a small group of people you know personally, this is meant to skip the full paid security assessment — see [Google's restricted scope verification docs](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)).

The classification and sync logic itself (`classifier.py`, `gmail_client.py`) doesn't need to change — it's already written per-user, it's just the auth/storage layer around it that needs to grow.

## Ideas to extend

- Swap keyword classification for an LLM call when you want higher accuracy on oddly-worded emails.
- Add a "last synced" timestamp and auto-sync on a schedule instead of manual clicking.
- Track response time per company (days between applying and first reply).
- Archive/hide cards instead of just recoloring them.
