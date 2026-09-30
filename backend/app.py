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

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

import db
from classifier import classify, extract_company, looks_job_related, extract_domain
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
CORS(app)

db.init_db()


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "tokenPresent": os.path.exists(
                os.path.join(os.path.dirname(__file__), "token.json")
            ),
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

    for msg in messages:
        domain = extract_domain(msg["sender"])
        if not looks_job_related(domain, msg["subject"], msg["body"]):
            skipped += 1
            continue

        status = classify(msg["subject"], msg["body"])
        company = extract_company(msg["sender"])

        db.upsert_application(
            {
                "id": msg["id"],
                "thread_id": msg["thread_id"],
                "sender": msg["sender"],
                "company": company,
                "subject": msg["subject"],
                "snippet": msg["snippet"],
                "status": status,
                "received_at": msg["received_at"],
            }
        )
        stored += 1

    return jsonify({"fetched": len(messages), "stored": stored, "skipped": skipped})


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
    )
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
