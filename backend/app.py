"""
Job Application Tracker API
----------------------------
Solo-MVP version: reads your own Gmail (read-only) for job-application-
related emails, classifies each with simple keyword rules, and stores the
results in a local SQLite database. No multi-user support yet - see
README for what changes when you're ready to add other people.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

import db
import llm
from classifier import extract_company, looks_job_related, extract_domain
from gmail_client import NotAuthorizedError, fetch_messages

load_dotenv()

DEFAULT_SEARCH_QUERY = (
    '(application OR applied OR interview OR "coding assessment" OR recruiter '
    "OR greenhouse.io OR lever.co OR myworkday.com OR ashbyhq.com OR icims.com) "
    "newer_than:180d"
)
SEARCH_QUERY = os.environ.get("GMAIL_SEARCH_QUERY", DEFAULT_SEARCH_QUERY)
MAX_RESULTS = int(os.environ.get("GMAIL_MAX_RESULTS", "50"))

app = Flask(__name__)

# Locked down to the deployed frontend's origin in production via
# ALLOWED_ORIGIN; unset (local dev) leaves it permissive.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN")
CORS(app, origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN else "*")

# Once deployed, every /api/* route is reachable by anyone on the internet
# and serves data pulled from your Gmail, so it's gated behind a shared
# secret (sent as the X-Api-Key header) whenever API_SECRET is set. Left
# unset for local dev, where nothing but your own machine can reach it.
API_SECRET = os.environ.get("API_SECRET")

db.init_db()


@app.before_request
def check_api_secret():
    if not API_SECRET or request.method == "OPTIONS":
        return
    if request.headers.get("X-Api-Key") != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "tokenPresent": os.path.exists(
                os.path.join(os.path.dirname(__file__), "token.json")
            )
            or bool(os.environ.get("GOOGLE_REFRESH_TOKEN")),
            "groqConfigured": llm.llm_available(),
        }
    )


@app.route("/api/sync", methods=["POST"])
def sync():
    """Fetch recent matching emails from Gmail, classify each one, and
    upsert into the database. Safe to call repeatedly - already-seen
    emails just get refreshed (unless you've manually corrected them)."""
    try:
        messages = fetch_messages(SEARCH_QUERY, max_results=MAX_RESULTS)
    except NotAuthorizedError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        # Catch-all so the frontend always gets a JSON error instead of
        # Flask's HTML debug traceback page (which breaks res.json() on
        # the frontend and shows a misleading "can't reach server" message).
        return jsonify({"error": f"Sync failed: {e}"}), 500

    stored = 0
    skipped = 0
    already_known = 0
    using_llm = llm.llm_available()
    existing_ids = db.get_existing_ids()

    for msg in messages:
        # Gmail message content is immutable, so once we've classified and
        # stored an email there's nothing to refresh - skip it entirely
        # rather than re-running the (rate-limited) LLM call on it every
        # single sync. This is what keeps a frequent auto-sync cheap.
        if msg["id"] in existing_ids:
            already_known += 1
            continue

        domain = extract_domain(msg["sender"])
        if not looks_job_related(domain, msg["subject"], msg["body"]):
            skipped += 1
            continue

        status, summary, position = llm.classify_and_summarize(msg["subject"], msg["body"])
        company = extract_company(msg["sender"])

        db.upsert_application(
            {
                "id": msg["id"],
                "thread_id": msg["thread_id"],
                "sender": msg["sender"],
                "company": company,
                "subject": msg["subject"],
                "snippet": msg["snippet"],
                "body": msg["body"],
                "body_html": msg["body_html"],
                "summary": summary,
                "position": position,
                "status": status,
                "received_at": msg["received_at"],
            }
        )
        stored += 1

        if using_llm:
            time.sleep(2.1)  # stay under Groq's free-tier ~30 req/min limit

    db.set_last_synced_at(datetime.now(timezone.utc).isoformat())

    return jsonify(
        {
            "fetched": len(messages),
            "stored": stored,
            "skipped": skipped,
            "alreadyKnown": already_known,
            "usedAi": using_llm,
        }
    )


@app.route("/api/last-sync", methods=["GET"])
def last_sync():
    return jsonify({"lastSyncedAt": db.get_last_synced_at()})


@app.route("/api/applications", methods=["GET"])
def list_applications():
    return jsonify(db.get_all_applications())


@app.route("/api/applications/<app_id>", methods=["PATCH"])
def update_application(app_id):
    payload = request.get_json(force=True) or {}
    db.update_application_fields(
        app_id,
        status=payload.get("status"),
        company=payload.get("company"),
        starred=payload.get("starred"),
    )
    return jsonify({"ok": True})


@app.route("/api/applications/dismiss", methods=["POST"])
def dismiss_applications():
    """Delete the given application ids - used when a category box is
    closed in the UI, to clear out emails you've already looked at.
    Starred emails are protected and never deleted, even if their id is
    included here (db.delete_unstarred re-checks server-side)."""
    payload = request.get_json(force=True) or {}
    ids = payload.get("ids") or []
    deleted = db.delete_unstarred(ids)
    return jsonify({"deleted": deleted})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
