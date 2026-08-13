import { useSyncExternalStore } from "react";

let running = false;
const listeners = new Set<() => void>();

export function setResearchRunActive(next: boolean): void {
  if (running === next) return;
  running = next;
  for (const listener of listeners) listener();
}

export function useResearchRunActive(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => running,
    () => false
  );
}
