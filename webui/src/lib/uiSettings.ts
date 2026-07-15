"use client";

import { useCallback, useEffect, useState } from "react";

const COLLAPSE_AGENT_ACTIONS_KEY = "jinwu.ui.collapseAgentActions";

function readCollapseAgentActions(): boolean {
  if (typeof window === "undefined") return true;
  const raw = window.localStorage.getItem(COLLAPSE_AGENT_ACTIONS_KEY);
  if (raw === null) return true; // default: collapse
  return raw === "true";
}

/**
 * Whether completed agent-action groups should auto-collapse once a turn
 * settles. Defaults to true. Stored in localStorage so the preference
 * persists across reloads. Standalone from `DeploymentConfig` because this
 * is a UI preference, not a server connection.
 */
export function useCollapseAgentActions(): {
  value: boolean;
  setValue: (next: boolean) => void;
} {
  const [value, setValueState] = useState<boolean>(readCollapseAgentActions);

  // Pick up changes from other tabs / late init (e.g. SSR → CSR transition).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== COLLAPSE_AGENT_ACTIONS_KEY) return;
      setValueState(readCollapseAgentActions());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setValue = useCallback((next: boolean) => {
    setValueState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(COLLAPSE_AGENT_ACTIONS_KEY, String(next));
    }
  }, []);

  return { value, setValue };
}
