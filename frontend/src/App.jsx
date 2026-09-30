import { useEffect, useState } from "react";
import DOMPurify from "dompurify";

// Harden links/images DOMPurify lets through: open external links safely,
// and avoid re-triggering tracking pixels' referrer leaks on every render.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
  if (node.tagName === "IMG") {
    node.setAttribute("referrerpolicy", "no-referrer");
    node.setAttribute("loading", "lazy");
  }
});

function sanitizeEmailHtml(rawHtml) {
  return DOMPurify.sanitize(rawHtml, {
    // "style" is forbidden too: emails often ship a dark-mode media query
    // in their <style> block that swaps text to a near-white color for a
    // dark background. Since we always render inside a forced-white box,
    // that would make the text invisible whenever the viewer's OS/browser
    // is in dark mode. Dropping <style> and relying on inline styles is
    // also closer to how Gmail itself renders emails.
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "input", "button", "link", "meta"],
  });
}

const STATUS_META = {
  interview_or_assessment: { label: "🎯  Interview / Assessment", order: 1 },
  offer: { label: "🎉  Offer", order: 2 },
  application_received: { label: "📩  Applied / No update", order: 3 },
  other: { label: "📧  Other", order: 4 },
  rejected: { label: "❌  Rejected", order: 5 },
};

const STATUS_OPTIONS = Object.entries(STATUS_META)
  .sort((a, b) => a[1].order - b[1].order)
  .map(([value, meta]) => ({ value, label: meta.label }));

// Configurable so the deployed frontend can point at the deployed backend
// instead of relying on Vite's local dev proxy. Both default to "" so
// local dev (no .env set) behaves exactly as before.
const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const API_SECRET = import.meta.env.VITE_API_SECRET || "";

// Note: this "secret" ends up readable in the deployed frontend's JS
// bundle (anyone can open devtools and see it) - it's not real access
// control, just enough to keep the API from being crawled/hit by bots
// scanning the open internet. Real protection is the backend itself
// requiring it on every request.
function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (API_SECRET) headers["X-Api-Key"] = API_SECRET;
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

function renderSummary(text, position) {
  if (!text) return text;
  if (!position) return text;
  const idx = text.indexOf(position);
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <span className="position-highlight">{position}</span>
      {text.slice(idx + position.length)}
    </>
  );
}

// How often the frontend quietly re-checks for updates from the
// background auto-sync (GitHub Actions, every 30 min) while a tab is left
// open - independent of that schedule, just keeps what's on screen fresh.
const POLL_INTERVAL_MS = 60_000;

export default function App() {
  const [authorized, setAuthorized] = useState(null);
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastSyncedAt, setLastSyncedAt] = useState(null);

  async function checkHealth() {
    try {
      const res = await apiFetch("/api/health");
      const data = await res.json();
      setAuthorized(Boolean(data.tokenPresent));
    } catch {
      setAuthorized(false);
    }
  }

  async function loadApplications() {
    try {
      const res = await apiFetch("/api/applications");
      const data = await res.json();
      setApplications(data);
    } finally {
      setLoading(false);
    }
  }

  async function loadLastSynced() {
    try {
      const res = await apiFetch("/api/last-sync");
      const data = await res.json();
      setLastSyncedAt(data.lastSyncedAt);
    } catch {
      // best-effort - the label just stays as whatever it last showed
    }
  }

  useEffect(() => {
    checkHealth();
    loadApplications();
    loadLastSynced();

    // Auto-sync now happens server-side on a schedule, not from a button
    // here - poll periodically so a left-open tab picks up an updated
    // timestamp without needing a manual refresh. Deliberately NOT
    // re-loading applications here: that list should only change what's on
    // screen on a full page reload (see handleCategoryToggle below), not
    // silently out from under someone mid-read every 60 seconds.
    const interval = setInterval(() => {
      loadLastSynced();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  async function handleToggleStar(id, starred) {
    setApplications((prev) =>
      prev.map((a) => (a.id === id ? { ...a, starred } : a))
    );
    await apiFetch(`/api/applications/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred }),
    });
  }

  // Closing a category box marks whatever (non-starred) emails were showing
  // in it for deletion server-side right away, so they don't keep piling up
  // in the database. But the visible list is deliberately left alone here -
  // they stay on screen until the next full page reload, rather than
  // vanishing the instant the box is closed. currentItems is read fresh on
  // every close, so it always reflects what was actually visible.
  async function handleCategoryToggle(currentItems, e) {
    if (e.target.open) return;

    const idsToDismiss = currentItems.filter((a) => !a.starred).map((a) => a.id);
    if (idsToDismiss.length === 0) return;

    try {
      await apiFetch("/api/applications/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: idsToDismiss }),
      });
    } catch {
      // best-effort - if this fails, they'll just still be there next reload
      // rather than silently losing anything.
    }
  }

  const grouped = STATUS_OPTIONS.map(({ value, label }) => ({
    value,
    label,
    items: applications.filter((a) => a.status === value),
  }));

  return (
    <div className="page">
      <header>
        <h1>Job Application Tracker</h1>
        <p className="subtitle">Auto-sorted from your inbox</p>
      </header>

      {authorized === false && (
        <div className="banner warning">
          Gmail isn't connected yet. In your terminal, run{" "}
          <code>python authorize.py</code> in the backend folder - the
          scheduled auto-sync will pick things up from there.
        </div>
      )}

      <div className="toolbar">
        <span className="sync-status">
          {lastSyncedAt ? (
            <>
              ✓ Last updated at{" "}
              {new Date(lastSyncedAt).toLocaleTimeString(undefined, {
                hour: "numeric",
                minute: "2-digit",
              })}
            </>
          ) : (
            "Waiting for the first sync..."
          )}
        </span>
      </div>

      {loading ? (
        <p className="muted">Loading...</p>
      ) : (
        <div className="category-list">
          {grouped.map((group) => (
            <details
              key={group.value}
              className="category-row"
              open={group.items.length > 0}
              onToggle={(e) => handleCategoryToggle(group.items, e)}
            >
              <summary>
                {group.label} <span className="category-count">({group.items.length})</span>
              </summary>
              <div className="category-items">
                {group.items.length === 0 && (
                  <p className="muted empty">Nothing here yet.</p>
                )}
                {group.items.map((app) => (
                  <ApplicationCard key={app.id} app={app} onToggleStar={handleToggleStar} />
                ))}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

function ApplicationCard({ app, onToggleStar }) {
  const received = app.received_at ? new Date(app.received_at) : null;
  const dateStr = received
    ? received.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : "";
  const timeStr = received
    ? received.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : "";

  const senderMatch = app.sender ? app.sender.match(/^(.*?)\s*<(.+)>\s*$/) : null;
  const senderName = senderMatch ? senderMatch[1] : app.sender;
  const senderEmail = senderMatch ? senderMatch[2] : null;

  return (
    <div className="card">
      <div className="card-top-row">
        <p className="card-timestamp">
          {dateStr} &middot; {timeStr}
        </p>
        <button
          type="button"
          className={`star-btn ${app.starred ? "starred" : ""}`}
          onClick={() => onToggleStar(app.id, !app.starred)}
          title={
            app.starred
              ? "Starred - won't be cleared when this box closes"
              : "Star to keep this email when this box closes"
          }
        >
          {app.starred ? "★" : "☆"}
        </button>
      </div>

      <p className="card-from">
        📨 <strong>From:</strong> {senderName}
        {senderEmail && <span className="sender-email"> ({senderEmail})</span>}
      </p>
      <p className="card-summary">📝 <strong>Summary:</strong> <em>{renderSummary(app.summary || app.snippet, app.position)}</em></p>

      <details className="full-email">
        <summary>Read full email</summary>
        {app.body_html ? (
          <div
            className="full-email-html"
            dangerouslySetInnerHTML={{ __html: sanitizeEmailHtml(app.body_html) }}
          />
        ) : app.body ? (
          <pre className="full-email-body">{app.body}</pre>
        ) : (
          <p className="muted">
            Full email not saved for this one yet - it'll show up after the
            next sync.
          </p>
        )}
      </details>
    </div>
  );
}
