import { getConfig } from "@/lib/config";

export interface SolarActivityMonthRow {
  month: string;
  sunspot_number?: number | null;
  sunspot_stddev?: number | null;
  sunspot_observations?: number | null;
  sunspot_provisional?: boolean;
  smoothed_sunspot_number?: number | null;
  smoothed_definitive?: boolean;
  f107?: number | null;
  provisional?: boolean;
}

export interface SolarCycleExtremum {
  cycle: number;
  minimum: {
    year: number;
    month: number;
    label: string;
    sunspot_number: number;
  };
  maximum: {
    year: number;
    month: number;
    label: string;
    sunspot_number: number;
  } | null;
  duration: {
    years: number;
    months: number;
    label: string;
  } | null;
  completed: boolean;
}

export interface SolarActivitySource {
  id: string;
  title_zh: string;
  provider: string;
  authority_url: string;
  fetched_at: string | null;
  cache_state: "fresh" | "stale";
  stale: boolean;
  license: string;
  refresh_error?: string;
}

export interface SolarActivityCapability {
  feature: string;
  state: "available" | "missing";
  note: string;
}

export interface SolarActivityOverview {
  status: string;
  generated_at: string;
  summary: {
    latest_sunspot: { month: string; value: number } | null;
    latest_smoothed_sunspot: { month: string; value: number } | null;
    latest_f107: { month: string; value: number } | null;
    current_cycle: SolarCycleExtremum | null;
    coverage_start: string | null;
    coverage_end: string | null;
  };
  monthly_rows: SolarActivityMonthRow[];
  cycles: SolarCycleExtremum[];
  sources: SolarActivitySource[];
  capabilities: SolarActivityCapability[];
}

export async function fetchSolarActivityOverview(options?: {
  refresh?: boolean;
  months?: number;
}): Promise<SolarActivityOverview> {
  const cfg = getConfig();
  if (!cfg) throw new Error("尚未配置 Deployment URL。");
  const base = cfg.deploymentUrl.replace(/\/$/, "");
  const params = new URLSearchParams({ months: String(options?.months ?? 24) });
  if (options?.refresh) params.set("refresh", "1");
  const headers: Record<string, string> = {};
  const apiKey =
    cfg.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  if (apiKey) headers["X-Api-Key"] = apiKey;
  const response = await fetch(
    `${base}/api/solar-activity/overview?${params.toString()}`,
    { headers, cache: "no-store" }
  );
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) message = body.error;
    } catch {
      // Keep the HTTP status message when the error body is not JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as SolarActivityOverview;
}
