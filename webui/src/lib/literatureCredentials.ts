import { getConfig } from "@/lib/config";

/**
 * Client for the local literature-credential surface
 * (`/api/literature/credentials`). Stored values never come back; the API
 * returns a mask, so this module never puts a secret in localStorage.
 */

export interface LiteratureCredential {
  provider: string;
  label: string;
  env: string;
  kind: "key" | "email";
  hint: string;
  configured: boolean;
  source: "environment" | "config" | "";
  masked: string;
  env_override: boolean;
}

export interface LiteratureCredentialCatalog {
  status: string;
  items: LiteratureCredential[];
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
  const r = await fetch(`${base}/api/literature/credentials`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const payload = (await r.json().catch(() => null)) as {
    error?: string;
    status?: string;
  } | null;
  if (!r.ok) {
    throw new Error(payload?.error || `HTTP ${r.status}`);
  }
  return payload as T;
}

export async function fetchLiteratureCredentials(): Promise<LiteratureCredentialCatalog> {
  return request<LiteratureCredentialCatalog>("GET");
}

export async function saveLiteratureCredentials(
  credentials: Record<string, string>
): Promise<LiteratureCredentialCatalog & { saved?: string[]; note?: string }> {
  return request("PUT", { credentials });
}
