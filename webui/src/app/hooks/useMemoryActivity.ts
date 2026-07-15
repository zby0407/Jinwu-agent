"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getMemorySeenAt, setMemorySeenAt } from "@/lib/memoryActivity";

const DEFAULT_INTERVAL_MS = 15_000;

interface UseMemoryActivityResult {
  /** Files newer than the seen-baseline (i.e. updates since the user last looked). */
  unseenCount: number;
  /** Epoch ms of the newest memory file. */
  latestMtime: number;
  /** Mark existing memory as seen (clears the badge). */
  markSeen: () => void;
  refresh: () => void;
}

/**
 * Polls /api/memory and reports how many memory files changed since the user last
 * opened the Memory panel — drives the Memory nav badge. Effect-based: it reads
 * the `mtime` the listing already returns rather than watching the ephemeral
 * memory-worker runs. Pauses while the tab is hidden.
 *
 * On first ever load (no stored baseline) it adopts the current newest mtime as
 * the baseline, so pre-existing memory doesn't show a badge — only changes from
 * here on do.
 */
export function useMemoryActivity(opts?: {
  enabled?: boolean;
  intervalMs?: number;
}): UseMemoryActivityResult {
  const enabled = opts?.enabled ?? true;
  const intervalMs = opts?.intervalMs ?? DEFAULT_INTERVAL_MS;
  const [latestMtime, setLatestMtime] = useState(0);
  const [unseenCount, setUnseenCount] = useState(0);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/memory", { cache: "no-store" });
      if (!res.ok) return;
      const data = (await res.json()) as {
        entries?: { mtime?: number }[];
      };
      if (!mountedRef.current) return;
      const mtimes = (data.entries ?? [])
        .map((e) => (typeof e.mtime === "number" ? e.mtime : 0))
        .filter((m) => m > 0);
      const latest = mtimes.length ? Math.max(...mtimes) : 0;
      let baseline = getMemorySeenAt();
      // First run: adopt current newest as baseline (don't badge old memory).
      if (baseline === 0 && latest > 0) {
        setMemorySeenAt(latest);
        baseline = latest;
      }
      setLatestMtime(latest);
      setUnseenCount(mtimes.filter((m) => m > baseline).length);
    } catch {
      // Offline / backend down — leave the last known counts in place.
    }
  }, []);

  const markSeen = useCallback(() => {
    // If the user opens Memory before the first poll has loaded, latestMtime is
    // still unknown. Use "now" as the baseline so old files are treated as seen
    // and only future edits can raise the badge again.
    setMemorySeenAt(latestMtime > 0 ? latestMtime : Date.now());
    setUnseenCount(0);
  }, [latestMtime]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      return () => {
        mountedRef.current = false;
      };
    }
    refresh();
    const interval = setInterval(() => {
      if (!document.hidden) refresh();
    }, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [refresh, enabled, intervalMs]);

  return { unseenCount, latestMtime, markSeen, refresh };
}
