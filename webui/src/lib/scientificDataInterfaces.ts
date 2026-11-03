import { getConfig } from "@/lib/config";

/**
 * Client for the local scientific-data interface surface
 * (`/api/scientific-data/interfaces`): PDF table OCR, chart reading and the
 * data-discovery providers.  Stored keys never come back; the backend returns
 * a mask, so this module never puts a secret in localStorage.
 */

export interface ScientificDataInterfaceItem {
  group: string;
  field: string;
  label: string;
  env: string;
  kind: "key" | "url" | "text" | "provider" | "list";
  hint: string;
  options: string[];
  configured: boolean;
  source: "environment" | "config" | "";
  masked: string;
  value: string;
  env_override: boolean;
}

export interface ScientificDataInterfaceGroup {
  id: string;
  label: string;
  description: string;
}

export interface ScientificDataInterfaceCatalog {
  status: string;
  groups: ScientificDataInterfaceGroup[];
  items: ScientificDataInterfaceItem[];
}

interface SaveResponse extends ScientificDataInterfaceCatalog {
  saved?: string[];
  note?: string;
  error?: string;
}

async function request<T>(method: "GET" | "PUT", body?: unknown): Promise<T> {
  const cfg = getConfig();
  if (!cfg) throw new Error("尚未配置 Deployment URL。");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const apiKey =
    cfg.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  if (apiKey) headers["X-Api-Key"] = apiKey;

  const base = cfg.deploymentUrl.replace(/\/$/, "");
  const r = await fetch(`${base}/api/scientific-data/interfaces`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const payload = (await r.json().catch(() => null)) as {
    error?: string;
  } | null;
  if (!r.ok) {
    throw new Error(payload?.error || `HTTP ${r.status}`);
  }
  return payload as T;
}

export async function fetchScientificDataInterfaces(): Promise<ScientificDataInterfaceCatalog> {
  return request<ScientificDataInterfaceCatalog>("GET");
}

export async function saveScientificDataInterfaces(
  settings: Record<string, string>
): Promise<SaveResponse> {
  return request<SaveResponse>("PUT", { settings });
}
