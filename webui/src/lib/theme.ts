export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

// Single source of truth for the storage key, imported by BOTH the server
// layout's pre-paint script and the client ThemeProvider. It must live in a
// module WITHOUT "use client": importing a value from a client module into the
// server layout turns it into a client-reference stub, which corrupts the inline
// script (it serializes as a throwing function, not the string).
export const THEME_STORAGE_KEY = "jw-theme";
export const DARK_QUERY = "(prefers-color-scheme: dark)";

/** Read the persisted theme, defaulting to "system" (also on SSR / storage errors). */
export function getStoredTheme(): Theme {
  if (typeof window === "undefined") return "system";
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" || stored === "system"
      ? stored
      : "system";
  } catch {
    return "system";
  }
}
