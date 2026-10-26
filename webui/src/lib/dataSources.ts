import { kbSend } from "@/lib/knowledgeBase";
import { getConfig } from "@/lib/config";

/**
 * Client for the observational data-source surface
 * (`${deploymentUrl}/api/data-sources`, mounted by
 * `jw/langgraph_dev/data_sources_api.py`).
 */

export interface DataSourceRegistration {
  registered_at: string | null;
  retrieved_at: string | null;
  data_path: string | null;
  bytes: number | null;
  project: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  row_count: number | null;
}

export interface ObservationalDataSource {
  id: string;
  relative: string;
  title_zh: string;
  provider: string;
  authority_url: string;
  enabled: boolean;
  registration: DataSourceRegistration | null;
}

export interface DataSourceCatalog {
  status: string;
  sources: ObservationalDataSource[];
}

async function getJson<T>(path: string): Promise<T> {
  const cfg = getConfig();
  if (!cfg) throw new Error("尚未配置 Deployment URL。");
  const base = cfg.deploymentUrl.replace(/\/$/, "");
  const headers: Record<string, string> = {};
  const apiKey =
    cfg.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  if (apiKey) headers["X-Api-Key"] = apiKey;
  const r = await fetch(`${base}${path}`, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function fetchDataSources(): Promise<DataSourceCatalog> {
  return getJson<DataSourceCatalog>("/api/data-sources");
}

export async function setDataSourceEnabled(
  datasetId: string,
  enabled: boolean
): Promise<{ id: string; enabled: boolean }> {
  return kbSend(`/api/data-sources/${encodeURIComponent(datasetId)}`, "PUT", {
    enabled,
  });
}
