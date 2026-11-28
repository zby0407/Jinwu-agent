export type SolarPoint = [number, number | null];

export type CycleArchive = {
  version: 1;
  frozenAt: string;
  monthly: [string, number | null][];
  smoothed: [string, number | null][];
  f107: [string, number][];
  cycles: { number: number; start: string; minimum: number; peakAt: string | null; peak: number | null }[];
};

export const solarCycleEndpoint = "/api/solar-cycle";

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
