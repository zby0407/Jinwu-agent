export const solarChannels = {
  "0193": { label: "193 Å", instrument: "SDO/AIA", description: "日冕极紫外" },
  "0171": { label: "171 Å", instrument: "SDO/AIA", description: "日冕极紫外" },
  "0304": { label: "304 Å", instrument: "SDO/AIA", description: "色球与过渡区极紫外" },
  HMII: { label: "连续谱", instrument: "SDO/HMI", description: "光球连续谱" },
  HMIB: { label: "磁图", instrument: "SDO/HMI", description: "视向磁场" },
} as const;

export type SolarChannel = keyof typeof solarChannels;
export type SolarFrame = { channel: SolarChannel; observedAt: string; url: string };
export type FlareEvent = {
  startAt: string;
  peakAt: string | null;
  endAt: string | null;
  grade: string | null;
};
export type SolarRegion = {
  number: number;
  location: string;
  spots: number | null;
  area: number | null;
  spotClass: string | null;
  magneticClass: string | null;
};
export type SolarPoint = [number, number | null];
export function latestPoint(points: SolarPoint[]) {
  for (let index = points.length - 1; index >= 0; index--) {
    if (points[index][1] !== null) return points[index];
  }
  return undefined;
}

type Cached<T> = T & { cachedAt: string };
export const productImageKeys = ["synoptic", "coronagraph", "farSide"] as const;
export type ProductImageKey = typeof productImageKeys[number];
export type ProductImage = Cached<{ url: string; observedAt: string | null; modifiedAt: string | null }>;
export type SolarProducts = Partial<{
  f107: Cached<{ points: SolarPoint[] }>;
  protons: Cached<{ gte10: SolarPoint[]; gte100: SolarPoint[] }>;
  wind: Cached<{ speed: SolarPoint[]; density: SolarPoint[]; sources: string[] }>;
  magnetic: Cached<{ bz: SolarPoint[]; sources: string[] }>;
  kp: Cached<{ points: SolarPoint[] }>;
  cme: Cached<{ issuedAt: string; events: { at: string; angle: number; speed: number }[] }>;
} & Record<ProductImageKey, ProductImage>>;
export type TodaySunSnapshot = {
  version: 1 | 2;
  date: string;
  fetchedAt: string;
  images: SolarFrame[];
  flux: { startAt: string; endAt: string; points: SolarPoint[] };
  flares: FlareEvent[];
  regions: { reportDate: string; rows: SolarRegion[] };
  products?: SolarProducts;
};

export type CycleArchive = {
  version: 1;
  frozenAt: string;
  monthly: [string, number | null][];
  smoothed: [string, number | null][];
  f107: [string, number][];
  cycles: { number: number; start: string; minimum: number; peakAt: string | null; peak: number | null }[];
};

export const todaySunEndpoint = "/api/today-sun";
export const solarCycleEndpoint = `${todaySunEndpoint}?archive=cycle`;

export async function readSolarCache<T>(url: string): Promise<T | null> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error("Could not read the solar cache");
  return response.json();
}

export function observationTime(value: string | number, zone = "Asia/Shanghai", withDate = true) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: zone,
    ...(withDate ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

export function beijingDay(date: Date): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(date);
}

export function strongestRecentFlare(snapshot: TodaySunSnapshot): FlareEvent | undefined {
  const start = Date.parse(snapshot.flux.startAt);
  const end = Date.parse(snapshot.flux.endAt);
  let strongest: FlareEvent | undefined;
  let max = 0;
  for (const flare of snapshot.flares) {
    if (!flare.grade || !flare.peakAt) continue;
    const time = Date.parse(flare.peakAt);
    const level = Number(flare.grade.slice(1)) * 10 ** "ABCMX".indexOf(flare.grade[0]);
    if (time >= start && time <= end && level > max) {
      strongest = flare;
      max = level;
    }
  }
  return strongest;
}
