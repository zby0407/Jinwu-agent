// Per-thread "auto-approve" preference, persisted in localStorage so it FOLLOWS
// each conversation: it survives switching views (Skills/Memory unmount the
// chat), switching to another thread, and page reloads. A thread that never
// turned it on simply has no entry (= off).
//
// The not-yet-created "New Chat" uses a sentinel key; once its first message
// creates a real thread id, `migrateNewThreadAutoApprove` carries the setting
// over to that id.

const STORAGE_KEY = "jinwu-auto-approve";
const NEW_THREAD_KEY = "__new__";

function keyFor(threadId: string | null): string {
  return threadId ?? NEW_THREAD_KEY;
}

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
    // Quota/private-mode failures are non-fatal — auto-approve just won't persist.
  }
}

/** Whether auto-approve is on for this thread (or the pending new chat). */
export function getThreadAutoApprove(threadId: string | null): boolean {
  return load()[keyFor(threadId)] === true;
}

/** Turn auto-approve on/off for this thread (or the pending new chat). */
export function setThreadAutoApprove(
  threadId: string | null,
  on: boolean
): void {
  const map = load();
  const key = keyFor(threadId);
  if (on) {
    map[key] = true;
  } else {
    // Store only "on" entries so absence == off and the map stays small.
    delete map[key];
  }
  save(map);
}

/**
 * When the pending new chat gets its real thread id, carry the sentinel setting
 * over to that id and clear the sentinel. No-op if it was never enabled.
 */
export function migrateNewThreadAutoApprove(newThreadId: string): void {
  const map = load();
  if (map[NEW_THREAD_KEY]) {
    map[newThreadId] = true;
  }
  if (NEW_THREAD_KEY in map) {
    delete map[NEW_THREAD_KEY];
    save(map);
  }
}
