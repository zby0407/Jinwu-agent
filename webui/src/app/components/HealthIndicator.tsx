"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

type HealthStatus = "checking" | "online" | "offline";

interface BackendInfo {
  version?: string;
  flags?: Record<string, boolean>;
}

interface HealthIndicatorProps {
  deploymentUrl: string;
  // Switch the app to a newly-detected backend URL (saves config + reconnects).
  // When omitted, the "reconnect to detected backend" affordance is hidden.
  onReconnect?: (url: string) => void;
}

const POLL_INTERVAL_MS = 10_000;
const REQUEST_TIMEOUT_MS = 4_000;

// Theme tokens (light + dark defined in globals.css). The base's shadcn
// primary/secondary background tokens are dead in this fork, so
// we reference CSS vars directly via arbitrary-value classes (the entries below).
// NOTE: never put a bracketed class literal in a comment — Tailwind's content
// scanner picks it up and emits real (sometimes invalid) CSS.
const STATUS_META: Record<
  HealthStatus,
  { dot: string; label: string; pulse: boolean }
> = {
  checking: {
    dot: "bg-[var(--color-warning)]",
    label: "连接中…",
    pulse: true,
  },
  online: {
    dot: "bg-[var(--color-success)]",
    label: "已连接",
    pulse: false,
  },
  offline: {
    dot: "bg-[var(--color-error)]",
    label: "离线",
    pulse: false,
  },
};

/** Strip a trailing slash so two URLs that differ only by it compare equal. */
function normalizeUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

/** Compact label for a URL, e.g. ":6174" or "host:8888", for a tight top bar. */
function shortUrlLabel(url: string): string {
  try {
    const u = new URL(url);
    const isLocal =
      u.hostname === "127.0.0.1" ||
      u.hostname === "localhost" ||
      u.hostname === "0.0.0.0";
    const port = u.port || (u.protocol === "https:" ? "443" : "80");
    return isLocal ? `:${port}` : `${u.hostname}:${port}`;
  } catch {
    return url;
  }
}

/** Probe a backend's unauthenticated GET /info. Resolves true iff it answers OK. */
async function probeInfo(base: string): Promise<boolean> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${base}/info`, {
      signal: controller.signal,
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Top-bar connection health light. Polls the backend's unauthenticated
 * `GET /info` (a "simple" cross-origin GET — no custom headers, so no CORS
 * preflight) and shows green/amber/red. Proves the UI can actually reach the
 * langgraph backend, which is the single most confusing failure mode
 * ("page opens but never replies"). Click to re-check on demand.
 *
 * Stale-URL recovery: when the saved deployment URL is unreachable, it re-probes
 * the backend's currently-detected port via `/api/jw-config`. If a DIFFERENT,
 * reachable URL is found (the classic "backend moved ports" case), it
 * surfaces a one-click "Reconnect" so the user doesn't have to hand-edit the URL
 * in Settings. Polling pauses while the tab is hidden and resumes on return.
 */
export function HealthIndicator({
  deploymentUrl,
  onReconnect,
}: HealthIndicatorProps) {
  const [status, setStatus] = useState<HealthStatus>("checking");
  const [info, setInfo] = useState<BackendInfo | null>(null);
  // A different, reachable backend URL detected while the saved one is dead.
  const [suggestedUrl, setSuggestedUrl] = useState<string | null>(null);
  // Monotonic id so a slow check against a previous URL can't clobber a newer one.
  const requestRef = useRef(0);
  // Cancel the in-flight probe and suppress state updates after unmount.
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);

  // When the saved URL is dead, ask our own /api/jw-config what port the
  // backend is actually configured on, then verify that alternate is reachable
  // before offering it. requestId ties the result to the check that spawned it.
  const findAlternate = useCallback(
    async (requestId: number) => {
      const current = normalizeUrl(deploymentUrl);
      try {
        const res = await fetch("/api/jw-config", { cache: "no-store" });
        const data = (await res.json().catch(() => null)) as {
          deploymentUrl?: string;
        } | null;
        const detected = data?.deploymentUrl
          ? normalizeUrl(data.deploymentUrl)
          : "";
        if (requestId !== requestRef.current || !mountedRef.current) return;
        // No alternate, or it's the same dead URL we already tried.
        if (!detected || detected === current) {
          setSuggestedUrl(null);
          return;
        }
        const reachable = await probeInfo(detected);
        if (requestId !== requestRef.current || !mountedRef.current) return;
        setSuggestedUrl(reachable ? detected : null);
      } catch {
        if (requestId === requestRef.current && mountedRef.current) {
          setSuggestedUrl(null);
        }
      }
    },
    [deploymentUrl]
  );

  const check = useCallback(async () => {
    const requestId = ++requestRef.current;
    const base = normalizeUrl(deploymentUrl);
    if (!base) {
      if (requestId === requestRef.current && mountedRef.current) {
        setInfo(null);
        setStatus("offline");
        setSuggestedUrl(null);
      }
      return;
    }

    // Abort any probe still in flight before starting a new one.
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/info`, {
        signal: controller.signal,
        cache: "no-store",
      });
      if (requestId !== requestRef.current || !mountedRef.current) return;
      if (!response.ok) {
        setStatus("offline");
        void findAlternate(requestId);
        return;
      }
      const data = (await response
        .json()
        .catch(() => null)) as BackendInfo | null;
      if (requestId !== requestRef.current || !mountedRef.current) return;
      setInfo(data);
      setStatus("online");
      // Connected — drop any stale reconnect suggestion.
      setSuggestedUrl(null);
    } catch {
      if (requestId === requestRef.current && mountedRef.current) {
        setStatus("offline");
        void findAlternate(requestId);
      }
    } finally {
      clearTimeout(timeout);
    }
  }, [deploymentUrl, findAlternate]);

  useEffect(() => {
    mountedRef.current = true;
    setStatus("checking");
    check();
    // Poll on an interval, but skip the network call while the tab is hidden —
    // there's no point probing a backend the user isn't looking at.
    const interval = setInterval(() => {
      if (!document.hidden) check();
    }, POLL_INTERVAL_MS);
    // Re-check when the tab regains focus/visibility or the network comes back,
    // so a transient outage (or a pause while hidden) clears without a refresh.
    const recheck = () => check();
    const onVisible = () => {
      if (!document.hidden) check();
    };
    window.addEventListener("focus", recheck);
    window.addEventListener("online", recheck);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      clearInterval(interval);
      window.removeEventListener("focus", recheck);
      window.removeEventListener("online", recheck);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [check]);

  const meta = STATUS_META[status];
  const title =
    status === "online"
      ? `已连接到 ${deploymentUrl}${
          info?.version ? ` · langgraph ${info.version}` : ""
        }`
      : status === "offline"
      ? `无法连接到 ${deploymentUrl} 的后端。请确认后端正在运行，点击可重试。`
      : `正在检查与 ${deploymentUrl} 的连接…`;

  const showReconnect =
    status === "offline" && suggestedUrl !== null && onReconnect !== undefined;

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        onClick={check}
        title={title}
        aria-label={title}
        className="h-8 gap-1.5 px-2 text-xs font-normal text-muted-foreground"
      >
        <span
          className={`size-2 shrink-0 rounded-full ${meta.dot} ${
            meta.pulse ? "animate-pulse" : ""
          }`}
          aria-hidden="true"
        />
        <span className="hidden sm:inline">{meta.label}</span>
      </Button>
      {showReconnect && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const url = suggestedUrl;
            if (!url) return;
            setSuggestedUrl(null);
            setStatus("checking");
            onReconnect?.(url);
          }}
          title={`检测到后端 ${suggestedUrl}，点击重新连接。`}
          aria-label={`重新连接到检测到的后端 ${suggestedUrl}`}
          className="h-8 gap-1 px-2 text-xs font-medium text-[var(--brand)] hover:text-[var(--brand)]"
        >
          重新连接{" "}
          <span className="tabular-nums">
            {suggestedUrl ? shortUrlLabel(suggestedUrl) : ""}
          </span>
        </Button>
      )}
    </div>
  );
}
