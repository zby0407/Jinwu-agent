"use client";

import { createContext, useContext, useMemo, useEffect, useState, ReactNode } from "react";
import { Client } from "@langchain/langgraph-sdk";

interface ClientContextValue {
  client: Client;
  runtime: RuntimeStatus;
}

interface RuntimeStatus {
  backendReady: boolean;
  canSend: boolean;
  agent?: { state: string; message?: string };
  maintenance?: boolean;
  elapsedMs: number;
}
const initialRuntime: RuntimeStatus = { backendReady: false, canSend: false, elapsedMs: 0 };

const ClientContext = createContext<ClientContextValue | null>(null);

interface ClientProviderProps {
  children: ReactNode;
  deploymentUrl: string;
  apiKey: string;
}

export function ClientProvider({
  children,
  deploymentUrl,
  apiKey,
}: ClientProviderProps) {
  const [runtime, setRuntime] = useState<RuntimeStatus>(initialRuntime);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const started = Date.now();
    setRuntime(initialRuntime);
    const poll = async () => {
      let next = { ...initialRuntime, elapsedMs: Date.now() - started };
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);
      try {
        const base = deploymentUrl.replace(/\/+$/, "");
        const headers = { "X-Api-Key": apiKey };
        const response = await fetch(`${base}/api/runtime-status`, { headers, signal: controller.signal, cache: "no-store" });
        if (response.ok) next = await response.json();
        else if (response.status === 404) {
          const legacy = await fetch(`${base}/info`, { headers, signal: controller.signal, cache: "no-store" });
          next = { ...next, backendReady: legacy.ok, canSend: legacy.ok };
        }
      } catch { /* Local readiness is separate from external model connectivity. */ }
      finally { clearTimeout(timeout); }
      if (cancelled) return;
      setRuntime(next);
      timer = setTimeout(poll, next.canSend ? 3000 : 1000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); controller?.abort(); };
  }, [deploymentUrl, apiKey]);
  const client = useMemo(() => {
    return new Client({
      apiUrl: deploymentUrl,
      defaultHeaders: {
        "Content-Type": "application/json",
        "X-Api-Key": apiKey,
      },
    });
  }, [deploymentUrl, apiKey]);

  const value = useMemo(() => ({ client, runtime }), [client, runtime]);

  return (
    <ClientContext.Provider value={value}>{children}</ClientContext.Provider>
  );
}

export function useRuntimeStatus(): RuntimeStatus {
  return useContext(ClientContext)?.runtime ?? initialRuntime;
}

export function useClient(): Client {
  const context = useContext(ClientContext);

  if (!context) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return context.client;
}
