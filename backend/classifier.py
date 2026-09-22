"""
Keyword-based classifier for job application emails.

This is deliberately simple: no ML, no external API calls. It looks for
common phrases used by ATS systems (Greenhouse, Lever, Workday, etc.) and
recruiters, and buckets each email into one status.

Order matters: a rejection email that also happens to mention "interview"
("we enjoyed interviewing you, but...") should still be classified as a
rejection, so rejection phrases are checked first.
"""

import re
from email.utils import parseaddr

# Each tuple is (status, [phrases]). Checked in this order - first match wins.
RULES = [
    (
        "rejected",
        [
            "unfortunately",
            "not moving forward",
            "will not be moving forward",
            "decided not to move forward",
            "decided not to proceed",
            "other candidates",
            "position has been filled",
            "not been selected",
            "not selected for this",
            "pursue other applicants",
            "wish you the best in your job search",
            "we regret to inform",
        ],
    ),
    (
        "offer",
        [
            "pleased to offer",
            "offer of employment",
            "excited to offer you",
            "extend an offer",
            "job offer",
        ],
    ),
    (
        "interview_or_assessment",
        [
            "schedule a call",
            "schedule an interview",
            "schedule a time",
            "online assessment",
            "coding assessment",
            "coding challenge",
            "technical interview",
            "phone screen",
            "next steps in the process",
            "would like to invite you",
            "book a time",
            "move forward with your application",
            "advance to the next round",
        ],
    ),
    (
        "application_received",
        [
            "thank you for applying",
            "thank you for your application",
            "we have received your application",
            "application has been received",
            "application was submitted",
            "confirming your application",
        ],
    ),
]

# Common ATS / recruiting platform sender domains. Used to help decide
# whether an email is even job-related in the first place, on top of the
# Gmail search query.
ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "workday.com",
    "icims.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "linkedin.com",
    "indeed.com",
    "wellfound.com",
    "breezy.hr",
]


def classify(subject: str, body: str) -> str:
    """Return one of: rejected, offer, interview_or_assessment,
    application_received, other."""
    text = f"{subject}\n{body}".lower()

    for status, phrases in RULES:
        for phrase in phrases:
            if phrase in text:
                return status

    return "other"


def looks_job_related(sender_domain: str, subject: str, body: str) -> bool:
    """Cheap pre-filter: is this even worth classifying/storing?"""
    domain = sender_domain.lower()
    if any(ats in domain for ats in ATS_DOMAINS):
        return True

    text = f"{subject}\n{body}".lower()
    job_signal_words = [
        "application",
        "applied",
        "interview",
        "position",
        "role",
        "candidate",
        "recruit",
        "hiring",
        "offer of employment",
        "assessment",
    ]
    return any(word in text for word in job_signal_words)


def extract_domain(email_address: str) -> str:
    match = re.search(r"@([\w.-]+)", email_address or "")
    return match.group(1) if match else ""


def extract_company(from_header: str) -> str:
    """Best-effort company name guess from a Gmail 'From' header.

    Prefers the display name ("Acme Corp Recruiting" <hr@acme.com>) since
    it's usually more human-readable than the sending domain, which for
    ATS platforms is often just "greenhouse.io" or "myworkday.com" rather
    than the actual company. Falls back to the domain when there's no
    display name. This is a guess, not a guarantee - the UI lets you
    correct it per application.
    """
    display_name, email_address = parseaddr(from_header or "")
    display_name = display_name.strip().strip('"')

    if display_name:
        return display_name

    domain = extract_domain(email_address)
    # Strip a leading "mail." / "no-reply." style subdomain if present
    parts = domain.split(".")
    return parts[0] if parts else domain
