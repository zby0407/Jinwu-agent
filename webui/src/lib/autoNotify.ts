// Per-thread "auto-report" preference: when on, a finished async sub-agent's
// result is AUTOMATICALLY looped back to the main agent (the same "[Async tasks
// update]" signal the manual "Notify main chat" button injects), so the main
// agent fetches it via check_async_task and integrates — like the TUI's auto
// report. ON by default (a conversation's background tasks loop back unless you
// turn it off); persisted in localStorage so the choice FOLLOWS each thread
// across reloads/views.
//
// Unlike auto-approve, this setting is read in ONE place (the toggle, in the
// Agents board) and CONSUMED in another (the auto-injection effect, on the chat
// view). So it ships a tiny pub/sub on top of localStorage: setting it notifies
// in-page subscribers immediately (custom event) and other tabs via `storage`.

const STORAGE_KEY = "jw-auto-notify";
const REPORTED_STORAGE_KEY = "jw-auto-notify-reported";
const CHANGE_EVENT = "jw-auto-notify-change";

function load(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, boolean>;
    }
  } catch {
    // Corrupt/unavailable storage → treat as empty (everything off).
  }
  return {};
}

function save(map: Record<string, boolean>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota/private-mode failures are non-fatal — it just won't persist.
  }
}

interface ReportedState {
  initialized: boolean;
  keys: string[];
}

function loadReported(): Record<string, ReportedState> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(REPORTED_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, ReportedState>;
    }
  } catch {
    // Corrupt/unavailable storage → rebuild the baseline when needed.
  }
  return {};
}

function saveReported(map: Record<string, ReportedState>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(REPORTED_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Persistence failure is non-fatal; the current session still dedups via UI.
  }
}

/** Whether auto-report is on for this thread. ON by default — a thread is off
 *  only if it was explicitly turned off. A null thread (pending new chat) reads
 *  off since it has no async tasks yet; once it gets a real id it defaults on. */
export function getThreadAutoNotify(threadId: string | null): boolean {
  if (!threadId) return false;
  return load()[threadId] !== false;
}

/** Turn auto-report on/off for a thread and notify subscribers (this tab + others). */
export function setThreadAutoNotify(
  threadId: string | null,
  on: boolean
): void {
  if (!threadId) return;
  const map = load();
  if (on) {
    // On is the default → drop the entry so the map stays small.
    delete map[threadId];
  } else {
    // Store only explicit "off" entries (absence == on == default).
    map[threadId] = false;
  }
  save(map);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }
}

/** Subscribe to auto-report changes (in-page via custom event, cross-tab via
 *  storage). Returns an unsubscribe function. */
export function subscribeAutoNotify(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener("storage", listener);
  };
}

export function getThreadAutoNotifyReportedKeys(
  threadId: string | null
): Set<string> {
  if (!threadId) return new Set();
  const state = loadReported()[threadId];
  return new Set(Array.isArray(state?.keys) ? state.keys : []);
}

export function isThreadAutoNotifyInitialized(
  threadId: string | null
): boolean {
  if (!threadId) return false;
  return loadReported()[threadId]?.initialized === true;
}

export function initializeThreadAutoNotifyReports(
  threadId: string | null,
  keys: Iterable<string>
): void {
  if (!threadId) return;
  const map = loadReported();
  const existing = map[threadId];
  map[threadId] = {
    initialized: true,
    keys: Array.from(new Set([...(existing?.keys ?? []), ...keys])),
  };
  saveReported(map);
}

export function markThreadAutoNotifyReported(
  threadId: string | null,
  key: string
): void {
  if (!threadId) return;
  const map = loadReported();
  const existing = map[threadId];
  map[threadId] = {
    initialized: existing?.initialized ?? true,
    keys: Array.from(new Set([...(existing?.keys ?? []), key])),
  };
  saveReported(map);
}
