"""
Postgres storage layer (a small free-tier hosted instance - Supabase/Neon -
works fine). No ORM - this app is simple enough that raw SQL is easier to
read than an abstraction over it.

DATABASE_URL is required (e.g. postgresql://user:pass@host:5432/dbname) -
set it the same way locally and on the deployed backend so both talk to
the same database and stay in sync.
"""

import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,           -- Gmail message id
    thread_id TEXT,
    sender TEXT,
    company TEXT,
    subject TEXT,
    snippet TEXT,
    body TEXT,                     -- full plain-text email body, fallback for the "read full email" view
    body_html TEXT,                -- raw HTML email body (sanitized client-side before rendering), NULL if the email had no HTML part
    summary TEXT,                  -- optional one-sentence AI summary (Groq), NULL if not used
    position TEXT,                 -- job title extracted from the summary (Groq), for highlighting in the UI
    status TEXT,                   -- rejected | offer | interview_or_assessment | application_received | other
    received_at TEXT,
    status_overridden INTEGER DEFAULT 0,
    company_overridden INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0      -- 1 = protected from the "dismiss on close" cleanup below
);
"""

# Single-row table (id is always 1) tracking when /api/sync last actually
# ran - separate from the applications themselves, since a sync that finds
# zero new emails still counts as "we checked".
SYNC_STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_status (
    id INTEGER PRIMARY KEY,
    last_synced_at TEXT
);
"""

# Columns added after the original CREATE TABLE went out - kept as
# ADD COLUMN IF NOT EXISTS so both a brand-new DB and an older one land on
# the same schema without needing separate migration logic.
MIGRATION_COLUMNS = [
    ("summary", "TEXT"),
    ("body", "TEXT"),
    ("position", "TEXT"),
    ("body_html", "TEXT"),
    ("starred", "INTEGER DEFAULT 0"),
]


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at your Postgres instance "
            "(see README) - both local dev and the deployed backend should "
            "use the same one so they share data."
        )
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(SYNC_STATUS_SCHEMA)
        for col, coltype in MIGRATION_COLUMNS:
            cur.execute(f"ALTER TABLE applications ADD COLUMN IF NOT EXISTS {col} {coltype}")
    conn.commit()
    conn.close()


def get_existing_ids() -> set:
    """All Gmail message ids already stored. Used by /api/sync to skip
    re-running the LLM classifier on emails it has already seen - Gmail
    message content is immutable, so there's nothing to refresh, and
    reclassifying the same batch on every auto-sync would just burn
    through Groq's free-tier quota for no benefit."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM applications")
        rows = cur.fetchall()
    conn.close()
    return {r["id"] for r in rows}


def upsert_application(row: dict):
    """Insert a new application, or update it - but never clobber a status
    or company the user has manually corrected."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status_overridden, company_overridden FROM applications WHERE id = %(id)s",
            row,
        )
        existing = cur.fetchone()

        if existing is None:
            cur.execute(
                """
                INSERT INTO applications
                    (id, thread_id, sender, company, subject, snippet, body, body_html, summary, position, status, received_at)
                VALUES (%(id)s, %(thread_id)s, %(sender)s, %(company)s, %(subject)s, %(snippet)s, %(body)s, %(body_html)s, %(summary)s, %(position)s, %(status)s, %(received_at)s)
                """,
                row,
            )
        else:
            set_clauses = ["thread_id = %(thread_id)s", "sender = %(sender)s", "subject = %(subject)s",
                            "snippet = %(snippet)s", "received_at = %(received_at)s", "summary = %(summary)s",
                            "body = %(body)s", "body_html = %(body_html)s", "position = %(position)s"]
            if not existing["status_overridden"]:
                set_clauses.append("status = %(status)s")
            if not existing["company_overridden"]:
                set_clauses.append("company = %(company)s")

            cur.execute(
                f"UPDATE applications SET {', '.join(set_clauses)} WHERE id = %(id)s",
                row,
            )

    conn.commit()
    conn.close()


def get_all_applications():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM applications ORDER BY received_at DESC")
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_application_fields(app_id: str, status: str = None, company: str = None, starred: bool = None):
    conn = get_connection()
    set_clauses = []
    params = {"id": app_id}

    if status is not None:
        set_clauses.append("status = %(status)s")
        set_clauses.append("status_overridden = 1")
        params["status"] = status

    if company is not None:
        set_clauses.append("company = %(company)s")
        set_clauses.append("company_overridden = 1")
        params["company"] = company

    if starred is not None:
        set_clauses.append("starred = %(starred)s")
        params["starred"] = 1 if starred else 0

    if not set_clauses:
        conn.close()
        return

    with conn.cursor() as cur:
        cur.execute(f"UPDATE applications SET {', '.join(set_clauses)} WHERE id = %(id)s", params)
    conn.commit()
    conn.close()


def get_last_synced_at():
    """ISO timestamp string of the last time /api/sync ran, or None if it
    has never run yet."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT last_synced_at FROM sync_status WHERE id = 1")
        row = cur.fetchone()
    conn.close()
    return row["last_synced_at"] if row else None


def set_last_synced_at(iso_timestamp: str):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_status (id, last_synced_at) VALUES (1, %(ts)s)
            ON CONFLICT (id) DO UPDATE SET last_synced_at = %(ts)s
            """,
            {"ts": iso_timestamp},
        )
    conn.commit()
    conn.close()


def delete_unstarred(ids: list) -> int:
    """Delete the given application ids, but skip any that are starred -
    starring is the escape hatch that protects an email from the
    'dismiss when its category box is closed' cleanup in the UI. Returns
    the number of rows actually deleted."""
    if not ids:
        return 0

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM applications WHERE id = ANY(%s) AND starred = 0",
            (ids,),
        )
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
