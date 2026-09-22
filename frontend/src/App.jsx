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

export default function App() {
  const [authorized, setAuthorized] = useState(null);
  const [groqConfigured, setGroqConfigured] = useState(false);
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState(null);

  async function checkHealth() {
    try {
      const res = await apiFetch("/api/health");
      const data = await res.json();
      setAuthorized(Boolean(data.tokenPresent));
      setGroqConfigured(Boolean(data.groqConfigured));
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

  useEffect(() => {
    checkHealth();
    loadApplications();
  }, []);

  async function handleSync() {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await apiFetch("/api/sync", { method: "POST" });
      const data = await res.json();

      if (!res.ok) {
        setAuthorized(false);
        setSyncMessage(data.error || "Sync failed.");
      } else {
        const aiNote = data.usedAi ? " (AI-classified)" : "";
        setSyncMessage(
          `Checked ${data.fetched} emails — stored ${data.stored}, skipped ${data.skipped} as not job-related.${aiNote}`
        );
        await loadApplications();
      }
    } catch {
      setSyncMessage("Could not reach the server. Is the backend running?");
    } finally {
      setSyncing(false);
    }
  }

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

  // Closing a category box clears out whatever (non-starred) emails were
  // showing in it, so old ones don't keep piling up. currentItems is read
  // fresh on every close, so it always reflects what was actually visible.
  async function handleCategoryToggle(currentItems, e) {
    if (e.target.open) return;

    const idsToDismiss = currentItems.filter((a) => !a.starred).map((a) => a.id);
    if (idsToDismiss.length === 0) return;

    setApplications((prev) => prev.filter((a) => !idsToDismiss.includes(a.id)));
    try {
      await apiFetch("/api/applications/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: idsToDismiss }),
      });
    } catch {
      // best-effort - if this fails, the next "Sync now" will just bring
      // them back rather than silently losing anything.
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
          <code>python authorize.py</code> in the backend folder, then click
          "Sync now" below.
        </div>
      )}

      <div className="toolbar">
        <button onClick={handleSync} disabled={syncing}>
          {syncing ? "Syncing..." : "Sync now"}
        </button>
        <span className={`ai-indicator ${groqConfigured ? "on" : "off"}`}>
          {groqConfigured ? "AI summaries: on" : "AI summaries: off"}
        </span>
        {syncMessage && <span className="sync-message">{syncMessage}</span>}
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
            Full email not saved for this one yet - click "Sync now" to fetch it.
          </p>
        )}
      </details>
    </div>
  );
}
