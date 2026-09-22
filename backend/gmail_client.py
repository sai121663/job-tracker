"""
Thin wrapper around the Gmail API: authentication and fetching/parsing
messages.

Two ways to authenticate, tried in this order:

1. Env vars (GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET) -
   used by the deployed backend, which has no browser to run an interactive
   OAuth flow and often an ephemeral filesystem to boot. The refresh token
   is generated once locally (see authorize.py) and copied into the host's
   env vars.
2. token.json on disk - the local-dev path. Run authorize.py once, which
   opens a browser, lets you approve access, and saves the result here.
   This module loads that saved token and refreshes it automatically.
"""

import base64
import html
import os
import re
import time
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
TOKEN_URI = "https://oauth2.googleapis.com/token"


class NotAuthorizedError(Exception):
    pass


def _load_credentials():
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        # Deployed/headless mode - no token.json, no browser. token=None is
        # fine here: get_gmail_service() below always exchanges the refresh
        # token for a fresh access token before the credentials are used.
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

    if not os.path.exists(TOKEN_PATH):
        raise NotAuthorizedError(
            "No token.json found. Run `python authorize.py` first (see README)."
        )
    return Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)


def get_gmail_service():
    """Build an authenticated Gmail API client, either from env vars (see
    module docstring) or the saved token.json.

    Raises NotAuthorizedError if neither is available/usable.
    """
    creds = _load_credentials()

    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            # Only env-var mode has nowhere to persist the refreshed token -
            # in token.json mode, write the refreshed one back so we don't
            # pay the refresh round-trip again next run.
            if not os.environ.get("GOOGLE_REFRESH_TOKEN"):
                with open(TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
        else:
            raise NotAuthorizedError(
                "Saved token is invalid and can't be refreshed. "
                "Delete token.json and re-run `python authorize.py`."
            )

    return build("gmail", "v1", credentials=creds)


def _get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _html_to_text(raw_html: str) -> str:
    """Very small HTML->text fallback for emails that only have an HTML
    part (no text/plain alternative) - not pixel-perfect, just readable."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw_html)
    text = re.sub(r"(?i)<(br|/p|/div|/tr|/li|/h[1-6])\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text.strip()


def _decode(data):
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("UTF-8")).decode(
        "UTF-8", errors="ignore"
    )


def _find_part(node, mime_type):
    if node.get("mimeType") == mime_type and node.get("body", {}).get("data"):
        return _decode(node["body"]["data"])
    for part in node.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return None


def _extract_bodies(payload):
    """Pull a readable body out of a Gmail message payload, returning
    (body_text, body_html).

    body_text prefers the text/plain part, falling back to the text/html
    part converted to plain text for HTML-only emails - it's what gets
    fed to the classifier and the LLM, and it's the fallback shown in
    the UI when there's no HTML part to render.

    body_html is the raw text/html part if the email has one, else None -
    it's what the frontend renders (sanitized) for a Gmail-like look."""

    plain = _find_part(payload, "text/plain")
    html_body = _find_part(payload, "text/html")

    if plain:
        body_text = plain
    elif html_body:
        body_text = _html_to_text(html_body)
    else:
        body_text = ""

    return body_text, html_body


def _get_message_with_backoff(service, msg_id, max_retries=5):
    """Fetch a single message, retrying with exponential backoff if Gmail's
    per-user rate limit kicks in (common when syncing a lot of emails at
    once, especially on a fresh/low-quota project)."""
    delay = 2
    for attempt in range(max_retries):
        try:
            return (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
        except HttpError as e:
            if e.resp.status in (403, 429) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def fetch_messages(query: str, max_results: int = 200):
    """Search Gmail with the given query and return parsed message dicts:
    {id, thread_id, sender, subject, snippet, body, body_html, received_at}."""

    service = get_gmail_service()

    results = []
    page_token = None

    while len(results) < max_results:
        resp = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                pageToken=page_token,
                maxResults=min(100, max_results - len(results)),
            )
            .execute()
        )
        message_stubs = resp.get("messages", [])
        if not message_stubs:
            break

        for stub in message_stubs:
            msg = _get_message_with_backoff(service, stub["id"])
            time.sleep(0.1)  # stay comfortably under Gmail's per-user rate limit

            headers = msg["payload"].get("headers", [])
            sender = _get_header(headers, "From")
            subject = _get_header(headers, "Subject")
            date_header = _get_header(headers, "Date")

            try:
                received_at = parsedate_to_datetime(date_header).isoformat()
            except (TypeError, ValueError):
                received_at = None

            body, body_html = _extract_bodies(msg["payload"])

            results.append(
                {
                    "id": msg["id"],
                    "thread_id": msg["threadId"],
                    "sender": sender,
                    "subject": subject,
                    "snippet": msg.get("snippet", ""),
                    "body": body,
                    "body_html": body_html,
                    "received_at": received_at,
                }
            )

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results
