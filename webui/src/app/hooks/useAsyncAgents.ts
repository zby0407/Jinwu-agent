"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useClient } from "@/providers/ClientProvider";
import {
  type EnrichedAsyncTask,
  parseAsyncTasks,
  isTerminalStatus,
} from "@/lib/asyncAgents";

const DEFAULT_INTERVAL_MS = 3_000;

/** The SDK throws HTTPError with a numeric `status` on non-2xx responses.
 *  404 on runs.get means the task's thread/run no longer exists — the backend
 *  restores only main-graph threads across restarts, so sub-agent threads
 *  vanish while the conversation's async_tasks entries survive. */
function isNotFoundError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { status?: unknown }).status === 404
  );
}

interface UseAsyncAgentsResult {
  tasks: EnrichedAsyncTask[];
  loaded: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * Watch the background async sub-agents a conversation launched.
 *
 * The conversation's `async_tasks` state carries each task's id/run, but its
 * `status` is only refreshed when the main agent calls check_async_task — so it
 * stays "running" indefinitely. To show accurate status (and stop the timer) we
 * read each task's REAL run status directly via `runs.get(thread_id, run_id)`.
 *
 * Polls only while `enabled` and the tab is visible. Shared by the Agents board
 * and the composer's "agents running" pulse so both agree.
 */
export function useAsyncAgents(
  threadId: string | null,
  opts?: { enabled?: boolean; intervalMs?: number }
): UseAsyncAgentsResult {
  const enabled = opts?.enabled ?? true;
  const intervalMs = opts?.intervalMs ?? DEFAULT_INTERVAL_MS;
  const client = useClient();
  const [tasks, setTasks] = useState<EnrichedAsyncTask[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Monotonic id so a slow poll can't overwrite a newer one (e.g. thread switch).
  const reqRef = useRef(0);
  const mountedRef = useRef(true);
  // Runs that already 404'd. A 404 is permanent for a given thread/run (run
  // registries are wiped on backend restart and never rebuilt), so remember
  // them and skip re-polling — no point hitting the backend with a 404 per
  // expired task every refresh cycle. Reset on thread/backend switch so a
  // different deployment gets probed fresh.
  const expiredRunsRef = useRef(new Set<string>());
  useEffect(() => {
    expiredRunsRef.current.clear();
  }, [client, threadId]);

  const refresh = useCallback(async () => {
    if (!enabled || !threadId) {
      setTasks([]);
      setLoaded(true);
      return;
    }
    const reqId = ++reqRef.current;
    try {
      const state = (await client.threads.getState(threadId)) as {
        values?: { async_tasks?: unknown };
      };
      const base = parseAsyncTasks(state.values?.async_tasks);
      // Resolve each task's REAL status from its own run, in parallel.
      const enriched = await Promise.all(
        base.map(async (t): Promise<EnrichedAsyncTask> => {
          if (!t.run_id) {
            return { ...t, liveStatus: t.status, startedAt: t.created_at };
          }
          const runKey = `${t.thread_id}:${t.run_id}`;
          if (expiredRunsRef.current.has(runKey)) {
            return {
              ...t,
              liveStatus: "expired",
              startedAt: t.created_at,
              endedAt: t.last_updated_at ?? t.created_at,
            };
          }
          try {
            const run = (await client.runs.get(t.thread_id, t.run_id)) as {
              status?: string;
              created_at?: string;
              updated_at?: string;
            };
            const liveStatus = run.status ?? t.status;
            return {
              ...t,
              liveStatus,
              startedAt: run.created_at ?? t.created_at,
              endedAt: isTerminalStatus(liveStatus)
                ? run.updated_at ?? t.last_updated_at
                : undefined,
            };
          } catch (err) {
            if (isNotFoundError(err)) {
              // The run is provably gone. The cached state status would say
              // "running" forever (it only refreshes when the main agent calls
              // check_async_task) — surface "expired" instead: gray dot, no
              // ticking timer, excluded from auto-report.
              expiredRunsRef.current.add(runKey);
              return {
                ...t,
                liveStatus: "expired",
                startedAt: t.created_at,
                endedAt: t.last_updated_at ?? t.created_at,
              };
            }
            // Transient failure (network blip, backend briefly down) — fall
            // back to the cached state status.
            return { ...t, liveStatus: t.status, startedAt: t.created_at };
          }
        })
      );
      if (reqId !== reqRef.current || !mountedRef.current) return;
      setTasks(enriched);
      setError(null);
    } catch {
      if (reqId === reqRef.current && mountedRef.current) {
        setError("Couldn't load background agents.");
      }
    } finally {
      if (reqId === reqRef.current && mountedRef.current) setLoaded(true);
    }
  }, [client, threadId, enabled]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      setTasks([]);
      setLoaded(true);
      return () => {
        mountedRef.current = false;
      };
    }
    setLoaded(false);
    refresh();
    const interval = setInterval(() => {
      if (!document.hidden) refresh();
    }, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [refresh, enabled, intervalMs]);

  return { tasks, loaded, error, refresh };
}
