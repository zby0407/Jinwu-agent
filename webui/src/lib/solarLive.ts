import { getConfig } from "@/lib/config";

/**
 * Client for the live solar dashboard surface
 * (`${deploymentUrl}/api/solar-live/*`, mounted by
 * `jw/langgraph_dev/solar_live_api.py`). The backend shares one cache with the
 * data-center GOES / solar-region query adapters.
 */

export type LivePoint = [number, number | null];
type Section<T> = ({ status: "ok" } & T) | { status: "error"; error: string };
export type FlareLevel = { flux: number | null; class: string | null };

export type LiveImageKey = "hmi" | "aia193" | "aia131" | "ccor1" | "ccor2" | "lasco";
export type LiveImageMeta = {
  label: string;
  instrument: string;
  description: string;
  credit: string;
  source_url: string;
  observed_at: string | null;
  fetched_at: string;
};

export type LiveRegion = {
  number: number;
  location: string;
  latitude: number;
  longitude_west: number;
  spots: number | null;
  area: number | null;
  spot_class: string | null;
  mag_class: string | null;
  c_probability: number | null;
  m_probability: number | null;
  x_probability: number | null;
  first_date: string | null;
  disk: { x: number; y: number; visible: boolean };
};

export type SolarLiveOverview = {
  status: string;
  generated_at: string;
  xray: Section<{
    points: LivePoint[];
    latest_at: string;
    current: FlareLevel;
    max_2h: FlareLevel;
    max_24h: FlareLevel;
    source_url: string;
  }>;
  flares: Section<{
    events: { begin_at: string; peak_at: string | null; end_at: string | null; class: string }[];
    source_url: string;
  }>;
  protons: Section<{
    series: { gte10: LivePoint[]; gte50: LivePoint[]; gte100: LivePoint[] };
    latest_gte10: number | null;
    s_scale: number;
    source_url: string;
  }>;
  regions: Section<{
    report_date: string;
    reference_at: string;
    projected_at: string;
    b0_deg: number;
    rows: LiveRegion[];
    source_url: string;
  }>;
  images: Record<LiveImageKey, Section<LiveImageMeta>>;
};

function backend() {
  const cfg = getConfig();
  if (!cfg) throw new Error("尚未配置 Deployment URL。");
  const apiKey = cfg.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  return {
    base: cfg.deploymentUrl.replace(/\/$/, ""),
    headers: apiKey ? { "X-Api-Key": apiKey } : undefined,
  };
}

export const solarLiveKey = "/api/solar-live/overview";
export const solarLiveOptions = {
  refreshInterval: 60_000,
  revalidateOnFocus: false,
  shouldRetryOnError: false,
};

export async function fetchSolarLive(): Promise<SolarLiveOverview> {
  const { base, headers } = backend();
  const response = await fetch(`${base}${solarLiveKey}`, { headers, cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as SolarLiveOverview;
}

export function liveImageUrl(key: LiveImageKey, meta: LiveImageMeta) {
  const version = encodeURIComponent(meta.observed_at ?? meta.fetched_at);
  return `${backend().base}/api/solar-live/image/${key}?v=${version}`;
}

/** Text colour for a GOES class, from quiet (A/B) to extreme (X). */
export function flareTone(label: string | null | undefined) {
  switch (label?.[0]) {
    case "X":
      return "text-fuchsia-700";
    case "M":
      return "text-red-600";
    case "C":
      return "text-orange-600";
    case "A":
    case "B":
      return "text-emerald-700";
    default:
      return "text-slate-500";
  }
}

export const flareColor: Record<string, string> = {
  A: "#047857",
  B: "#047857",
  C: "#ea580c",
  M: "#dc2626",
  X: "#a21caf",
};

const greek: Record<string, string> = { A: "α", B: "β", G: "γ", D: "δ" };

/** Mount Wilson class in Greek letters, e.g. ``BGD`` → ``βγδ``. */
export function magneticLabel(value: string | null | undefined) {
  if (!value) return null;
  return [...value.toUpperCase()].map((letter) => greek[letter] ?? letter).join("");
}

/** Orthographic projection in solar radii (x → west, y → north). */
export function projectDisk(latitude: number, westLongitude: number, b0: number) {
  const rad = Math.PI / 180;
  const [lat, lon, tilt] = [latitude * rad, westLongitude * rad, b0 * rad];
  return {
    x: Math.cos(lat) * Math.sin(lon),
    y: Math.sin(lat) * Math.cos(tilt) - Math.cos(lat) * Math.cos(lon) * Math.sin(tilt),
    visible: Math.cos(lat) * Math.cos(lon) * Math.cos(tilt) + Math.sin(lat) * Math.sin(tilt) > -1e-9,
  };
}

/* ---------- historical browse archive ---------- */

export type HistoryChannelKey = "hmi" | "hmimag" | "aia193" | "aia131";

export type SolarHistoryListing = {
  status: string;
  date: string;
  channels: Record<HistoryChannelKey, {
    label: string;
    instrument: string;
    times: string[];
    count: number;
  }>;
  source_url: string;
};

export async function fetchSolarHistory(date: string): Promise<SolarHistoryListing> {
  const { base, headers } = backend();
  const response = await fetch(`${base}/api/solar-live/history/${date}`, { headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as SolarHistoryListing;
}

export function historyImageUrl(channel: HistoryChannelKey, date: string, time: string) {
  return `${backend().base}/api/solar-live/history/image/${channel}/${date.replaceAll("-", "")}_${time}`;
}

/** "HHMMSS" → minutes of day, for nearest-frame snapping. */
export function historyTimeMinutes(stamp: string) {
  return Number(stamp.slice(0, 2)) * 60 + Number(stamp.slice(2, 4)) + Number(stamp.slice(4, 6)) / 60;
}

export function formatHistoryTime(stamp: string) {
  return `${stamp.slice(0, 2)}:${stamp.slice(2, 4)} UTC`;
}
