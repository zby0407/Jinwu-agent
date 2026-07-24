// Surfacing memory-worker ACTIVITY by its effect (files changed in
// ~/.jw/memories), not by watching the ~2s ephemeral worker runs.
// Two signals:
//   A) a "new since you last looked" badge on the Memory nav (seen-baseline)
//   B) a "recently updated" highlight inside the Memory panel (absolute recency)
// Both derive from the `mtime` already returned by /api/memory — zero backend
// changes.

const SEEN_KEY = "jw-memory-seen-at";

/** Files modified within this window are flagged "recently updated" in the panel. */
export const MEMORY_RECENT_MS = 10 * 60 * 1000;

/** Epoch ms of the newest memory file the user has already seen (0 if never). */
export function getMemorySeenAt(): number {
  if (typeof window === "undefined") return 0;
  const v = Number(localStorage.getItem(SEEN_KEY));
  return Number.isFinite(v) ? v : 0;
}

export function setMemorySeenAt(ts: number): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(SEEN_KEY, String(ts));
  } catch {
    // Quota/private-mode failures are non-fatal — the badge just won't persist.
  }
}

export function isRecent(mtime: number, now: number): boolean {
  return mtime > 0 && now - mtime < MEMORY_RECENT_MS;
}

/** Compact "just now" / "2m ago" / "3h ago" / "5d ago" from an epoch-ms mtime. */
export function relativeTime(ms: number, now: number): string {
  if (!ms) return "";
  const secs = Math.max(0, Math.round((now - ms) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
