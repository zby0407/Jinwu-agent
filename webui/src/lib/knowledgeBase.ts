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
  literature_deltas: number;
  literature_baseline_sources: number;
  literature_task_bundles: number;
  literature_impacts: number;
  wiki_patch_proposals: number;
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

export interface KbBuiltInCatalogEntry {
  id: string;
  type: string;
  module: string;
  title_zh: string;
  state: "seeded" | "planned" | "candidate" | "canonical" | string;
  priority: "P0" | "P1" | "P2" | string;
  path: string;
  live: KbEntrySummary | null;
}

export interface KbBuiltInTaskBundle {
  id: string;
  title_zh: string;
  purpose_zh: string;
  modules: string[];
  seed_entries: KbBuiltInCatalogEntry[];
  missing_seed_paths: string[];
  live_count: number;
}

export interface KbBuiltInWiki {
  available: boolean;
  error?: string;
  wiki_id: string;
  version: string;
  status: string;
  language: string;
  design_basis: string;
  purpose: {
    primary_stage?: string;
    consumer?: string;
    loading_strategy?: string;
    statement_zh?: string;
    boundary_zh?: string;
  };
  scope: {
    primary?: string[];
    secondary?: string[];
    out_of_scope?: string[];
  };
  always_load: Array<{ path: string; title: string }>;
  task_bundles: KbBuiltInTaskBundle[];
  catalog_entries: KbBuiltInCatalogEntry[];
  stats: {
    catalog_total: number;
    seeded_total: number;
    seeded_live: number;
    canonical_live: number;
    planned_total: number;
    task_bundle_total: number;
    state_counts: Record<string, number>;
    module_counts: Record<string, Record<string, number>>;
  };
}

export interface KbSourceSummary {
  source_id: string;
  family_id: string;
  canonical_source_id: string;
  provider: string;
  source_version: string;
  content_fingerprint: string;
  title: string;
  authors: string[];
  year: number | null;
  publication_date: string;
  doi: string;
  url: string;
  is_refereed: boolean;
  is_retracted: boolean;
  abstract_chars: number;
  fetched_at: string | null;
  first_seen_at: string | null;
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

export interface KbLiteratureFeedRun {
  id: number;
  feed_id: string;
  providers: string[];
  status: "ok" | "partial" | "unavailable";
  result_count: number;
  new_source_count: number;
  new_family_count: number;
  diagnostics: Record<string, unknown>;
  started_at: string;
  completed_at: string;
}

export interface KbLiteratureFeed {
  id: string;
  title_zh: string;
  query: string;
  providers: string[];
  lookback_years: number;
  sort: "relevance" | "recent";
  limit: number;
  enabled: boolean;
  source_count: number;
  latest_run: KbLiteratureFeedRun | null;
}

export interface KbLiteratureFeedCatalog {
  status: "ok" | "unavailable";
  schema_version?: string;
  total_sources: number;
  feeds: KbLiteratureFeed[];
  notice?: string;
  diagnostic?: string;
}

export interface KbLiteratureDelta {
  id: number;
  event_key: string;
  event_type:
    | "baseline_source"
    | "new_source"
    | "new_version"
    | "metadata_updated"
    | "source_retracted"
    | "feed_discovered"
    | "feed_removed"
    | string;
  source_id: string;
  family_id: string;
  feed_id: string;
  prior_source_version: string;
  source_version: string;
  prior_fingerprint: string;
  source_fingerprint: string;
  payload: Record<string, unknown>;
  detected_at: string;
}

export interface KbLiteratureImpact {
  id: number;
  source_id: string;
  family_id: string;
  entry_id: string;
  relation: "supports" | "contradicts" | "qualifies" | "extends" | string;
  affected_fields: string[];
  scope: Record<string, unknown>;
  quote: string;
  location: string;
  rationale: string;
  confidence: "low" | "medium" | string;
  status: string;
  entry_title?: string;
  source_title?: string;
  created_at: string;
  updated_at: string;
}

export interface KbWikiCandidatePatch {
  patch_id: string;
  target_entry_id: string;
  base_version: number;
  source_id: string;
  family_id: string;
  impact_id: number;
  relation: string;
  patch: {
    content?: Record<string, unknown>;
    valid_range?: string | null;
    rationale?: string;
  };
  patch_sha256: string;
  status: "proposal_only" | "stale" | string;
  entry_title?: string;
  source_title?: string;
  created_at: string;
  updated_at: string;
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

export async function fetchKbBuiltInWiki(): Promise<KbBuiltInWiki> {
  return kbFetch<KbBuiltInWiki>("/api/kb/builtin");
}

export async function fetchKbEntry(id: string): Promise<KbEntryDetail> {
  return kbFetch<KbEntryDetail>(`/api/kb/entries/${encodeURIComponent(id)}`);
}

export async function fetchKbSources(filters: {
  provider?: string;
  state?: string;
  q?: string;
  feed_id?: string;
  limit?: number;
}): Promise<KbSourceSummary[]> {
  const params = new URLSearchParams();
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.state) params.set("state", filters.state);
  if (filters.q) params.set("q", filters.q);
  if (filters.feed_id) params.set("feed_id", filters.feed_id);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbSourceSummary[]>(`/api/kb/sources${qs ? `?${qs}` : ""}`);
}

export async function fetchKbLiteratureFeeds(): Promise<KbLiteratureFeedCatalog> {
  return kbFetch<KbLiteratureFeedCatalog>("/api/kb/literature/feeds");
}

export async function fetchKbLiteratureDeltas(filters: {
  event_type?: string;
  feed_id?: string;
  source_id?: string;
  include_baseline?: boolean;
  limit?: number;
} = {}): Promise<KbLiteratureDelta[]> {
  const params = new URLSearchParams();
  if (filters.event_type) params.set("event_type", filters.event_type);
  if (filters.feed_id) params.set("feed_id", filters.feed_id);
  if (filters.source_id) params.set("source_id", filters.source_id);
  if (filters.include_baseline) params.set("include_baseline", "true");
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbLiteratureDelta[]>(
    `/api/kb/literature/deltas${qs ? `?${qs}` : ""}`
  );
}

export async function fetchKbLiteratureImpacts(filters: {
  entry_id?: string;
  source_id?: string;
  status?: string;
  limit?: number;
} = {}): Promise<KbLiteratureImpact[]> {
  const params = new URLSearchParams();
  if (filters.entry_id) params.set("entry_id", filters.entry_id);
  if (filters.source_id) params.set("source_id", filters.source_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbLiteratureImpact[]>(
    `/api/kb/literature/impacts${qs ? `?${qs}` : ""}`
  );
}

export async function fetchKbWikiPatches(filters: {
  status?: string;
  entry_id?: string;
  limit?: number;
} = {}): Promise<KbWikiCandidatePatch[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.entry_id) params.set("entry_id", filters.entry_id);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return kbFetch<KbWikiCandidatePatch[]>(
    `/api/kb/wiki/patches${qs ? `?${qs}` : ""}`
  );
}

export async function fetchKbSource(id: string): Promise<KbSourceDetail> {
  return kbFetch<KbSourceDetail>(`/api/kb/sources/${encodeURIComponent(id)}`);
}

export async function fetchKbGraph(limit = 200): Promise<KbGraph> {
  return kbFetch<KbGraph>(`/api/kb/graph?limit=${limit}`);
}

export async function fetchKbUsage(runId = ""): Promise<KbUsageRow[]> {
  const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return kbFetch<KbUsageRow[]>(`/api/kb/usage${qs}`);
}
