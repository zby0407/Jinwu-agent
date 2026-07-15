/**
 * Copy text to the clipboard, with a fallback, never throwing.
 *
 * `navigator.clipboard.writeText` rejects with `NotAllowedError` in several
 * everyday situations even on localhost — the document isn't focused, a
 * Permissions-Policy blocks `clipboard-write`, or the page is served over plain
 * HTTP on a LAN IP (non-secure context, where `navigator.clipboard` may be
 * absent entirely). An unhandled rejection there surfaces as a scary runtime
 * error overlay. So we try the async API first and, on any failure, fall back to
 * a hidden `<textarea>` + `document.execCommand("copy")`, which needs no async
 * permission and works in those degraded contexts.
 *
 * Returns whether the copy succeeded.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied / not focused / blocked — fall through to the legacy path.
  }
  try {
    if (typeof document === "undefined") return false;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    // Keep it off-screen and non-disruptive while still selectable.
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    try {
      ta.select();
      ta.setSelectionRange(0, text.length);
      return document.execCommand("copy");
    } finally {
      // Always remove the hidden textarea, even if execCommand throws.
      ta.remove();
    }
  } catch {
    return false;
  }
}
