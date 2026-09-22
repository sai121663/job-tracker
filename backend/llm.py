"""
Optional LLM-based classification + summarization, using Groq's free API
(OpenAI-compatible chat completions endpoint, model openai/gpt-oss-120b by
default). This is entirely optional: if GROQ_API_KEY isn't set, or a call
fails after retries, everything falls back to the plain keyword classifier
in classifier.py (just without a summary) - the app keeps working either
way, it just gets smarter when Groq is configured.
"""

import json
import os
import time

import requests

from classifier import classify as keyword_classify

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

VALID_STATUSES = {
    "rejected",
    "offer",
    "interview_or_assessment",
    "application_received",
    "other",
}

SYSTEM_PROMPT = (
    "You read job-application-related emails and respond with ONLY a JSON "
    'object with exactly three keys: "status" (one of: rejected, offer, '
    'interview_or_assessment, application_received, other), "summary" '
    "(a single sentence, 10-15 words, describing what this email says), and "
    '"position" (the specific job title/position mentioned in the email, '
    "copied EXACTLY as it appears in your summary text - same spelling, "
    "capitalization and spacing, so it can be found as a substring of the "
    "summary - or null if no specific job title is mentioned). "
    "Respond with nothing but that JSON object."
)


def llm_available() -> bool:
    return bool(GROQ_API_KEY)


def classify_and_summarize(subject: str, body: str, max_retries: int = 3):
    """Returns (status, summary, position). Falls back to the keyword
    classifier (with summary=None, position=None) if Groq isn't configured,
    or if every retry fails."""

    if not llm_available():
        return keyword_classify(subject, body), None, None

    user_content = f"Subject: {subject}\n\nBody:\n{body[:3000]}"

    delay = 2
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
                timeout=20,
            )

            if resp.status_code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            status = parsed.get("status", "other")
            if status not in VALID_STATUSES:
                status = "other"
            summary = parsed.get("summary") or None
            position = parsed.get("position") or None

            # Only keep it if it actually matches part of the summary -
            # otherwise the frontend has nothing to highlight against.
            if position and summary and position not in summary:
                position = None

            return status, summary, position

        except Exception:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            # Every retry failed - fall back rather than breaking the sync.
            return keyword_classify(subject, body), None, None

    return keyword_classify(subject, body), None, None
