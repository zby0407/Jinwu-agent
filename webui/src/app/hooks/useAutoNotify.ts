"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getThreadAutoNotify,
  setThreadAutoNotify,
  subscribeAutoNotify,
} from "@/lib/autoNotify";

/**
 * Reactive per-thread "auto-report finished async agents to the main chat"
 * preference. Stays in sync across components (the toggle in the Agents board
 * and the auto-injection effect on the chat view both use this) and across tabs.
 */
export function useAutoNotify(
  threadId: string | null
): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => getThreadAutoNotify(threadId));

  useEffect(() => {
    const sync = () => setOn(getThreadAutoNotify(threadId));
    sync();
    return subscribeAutoNotify(sync);
  }, [threadId]);

  const set = useCallback(
    (next: boolean) => setThreadAutoNotify(threadId, next),
    [threadId]
  );

  return [on, set];
}
