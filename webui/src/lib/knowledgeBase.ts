import { getConfig } from "@/lib/config";

/**
 * Client for the backend's read-only knowledge-base REST surface
 * (`GET ${deploymentUrl}/api/kb/*`, mounted by
 * `jw/langgraph_dev/knowledge_api.py`). URL resolution mirrors
 * `useAvailableModels`: the configured deployment URL + optional API key,
 * no hardcoded origin.
 */

export interface KbEntrySummary {
  id: string;
  type: string;
  title: string;
  status: string;
  confidence: string;
  valid_range: string;
  updated_at: string;
  source_ref: string;
}

export interface KbEntryVersion {
  version: number;
  changed_at: string;
  changed_by: string;
  reason: string;
}

export interface KbEntryDetail {
  id: string;
  type: string;
  title: string;
  content: Record<string, unknown>;
  source_type: string;
  source_ref: string;
  confidence: string;
  status: string;
  valid_range: string;
  related_ids: unknown[];
  provenance: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  versions: KbEntryVersion[];
  version_count: number;
  evidence: Record<string, KbEvidenceItem>;
  evidence_gaps: Array<{ field?: string; note?: string }>;
  related_entries: Array<{
    id: string;
    type: string;
    title: string;
    status: string;
  }>;
  source: KbSourceSummary | null;
}

export interface KbEvidenceItem {
  quote?: string;
  location?: string;
  text?: string;
}

export interface KbOverviewGap {
  code: string;
  label: string;
  count: number;
  severity: "high" | "medium" | "low";
  hint: string;
}

export interface KbOverview {
  entries: number;
  sources: number;
  source_families: number;
  fetched_sources: number;
  distilled_sources: number;
  distillations: number;
  pending_reviews: number;
  usage_reads: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_provider: Record<string, number>;
  coverage: {
    fetch_rate: number;
    distillation_rate: number;
    canonical_rate: number;
  };
  gaps: KbOverviewGap[];
}

export interface KbSourceSummary {
  source_id: string;
  family_id: string;
  canonical_source_id: string;
  provider: string;
  source_version: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string;
  url: string;
  abstract_chars: number;
  fetched_at: string | null;
  last_seen_at: string | null;
  distillation_count: number;
  stage: "cached" | "fetched" | "distilled";
}

export interface KbSourceDetail extends KbSourceSummary {
  abstract: string;
  distillations: Array<{
    focus: string;
    research_question: string;
    entry_id: string;
    relevance: string;
    created_at: string;
    entry_type: string | null;
    entry_title: string | null;
    entry_status: string | null;
    entry_confidence: string | null;
  }>;
}

export interface KbGraphNode {
  id: string;
  kind: "entry" | "source";
  type: string;
  title: string;
  status: string;
  confidence: string;
  source_type: string;
  degree: number;
  evidence_count?: number;
  source_id?: string;
  family_id?: string;
  provider?: string;
  year?: number | null;
  doi?: string;
  url?: string;
  abstract_chars?: number;
}

export interface KbGraphEdge {
  source: string;
  target: string;
  relation: "related_to" | "distilled_into" | "shares_source" | string;
  weight: number;
  signal: string;
}

export interface KbGraph {
  nodes: KbGraphNode[];
  edges: KbGraphEdge[];
  stats: {
    nodes: number;
    edges: number;
    orphans: number;
    entry_nodes?: number;
    source_nodes?: number;
  };
}

export interface KbReviewItem {
  id: number;
  kind: string;
  entry_id: string;
  payload: Record<string, unknown>;
  status: string;
  reviewer: string;
  decided_at: string | null;
  note: string;
}

export interface KbUsageRow {
  id: number;
  run_id: string;
  agent: string;
  entry_id: string;
  purpose: string;
  ts: string;
  entry_title: string | null;
}

function kbRequest(path: string): {
  url: string;
  headers: Record<string, string>;
} {
  const cfg = getConfig();
  if (!cfg) throw new Error("尚未配置 Deployment URL。");
  const base = cfg.deploymentUrl.replace(/\/$/, "");
  const headers: Record<string, string> = {};
  const apiKey =
    cfg.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  if (apiKey) headers["X-Api-Key"] = apiKey;
  return { url: `${base}${path}`, headers };
}

async function kbFetch<T>(path: string): Promise<T> {
  const { url, headers } = kbRequest(path);
  const r = await fetch(url, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function fetchKbEntries(filters: {
  type?: string;
  status?: string;
  q?: string;
  limit?: number;
}): Promise<KbEntrySummary[]> {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbEntrySummary[]>(`/api/kb/entries${qs ? `?${qs}` : ""}`);
}

export async function fetchKbOverview(): Promise<KbOverview> {
  return kbFetch<KbOverview>("/api/kb/overview");
}

export async function fetchKbEntry(id: string): Promise<KbEntryDetail> {
  return kbFetch<KbEntryDetail>(`/api/kb/entries/${encodeURIComponent(id)}`);
}

export async function fetchKbSources(filters: {
  provider?: string;
  state?: string;
  q?: string;
  limit?: number;
}): Promise<KbSourceSummary[]> {
  const params = new URLSearchParams();
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.state) params.set("state", filters.state);
  if (filters.q) params.set("q", filters.q);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbSourceSummary[]>(`/api/kb/sources${qs ? `?${qs}` : ""}`);
}

export async function fetchKbSource(id: string): Promise<KbSourceDetail> {
  return kbFetch<KbSourceDetail>(`/api/kb/sources/${encodeURIComponent(id)}`);
}

export async function fetchKbGraph(limit = 200): Promise<KbGraph> {
  return kbFetch<KbGraph>(`/api/kb/graph?limit=${limit}`);
}

export async function fetchKbReviewQueue(
  status = "pending"
): Promise<KbReviewItem[]> {
  return kbFetch<KbReviewItem[]>(
    `/api/kb/review_queue?status=${encodeURIComponent(status)}`
  );
}

export async function fetchKbUsage(runId = ""): Promise<KbUsageRow[]> {
  const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return kbFetch<KbUsageRow[]>(`/api/kb/usage${qs}`);
}
