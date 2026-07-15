/** Cron expression helpers for the Scheduled Tasks panel. */

export type Frequency = "daily" | "weekly" | "monthly" | "custom";

export interface ScheduleSpec {
  frequency: Frequency;
  hour: number;
  minute: number;
  /** 0 = Sunday … 6 = Saturday (used when frequency === "weekly") */
  dayOfWeek: number;
  /** 1–28 (used when frequency === "monthly") */
  dayOfMonth: number;
  /** Raw cron string (used when frequency === "custom") */
  custom: string;
}

export const DEFAULT_SCHEDULE_SPEC: ScheduleSpec = {
  frequency: "daily",
  hour: 9,
  minute: 0,
  dayOfWeek: 1,
  dayOfMonth: 1,
  custom: "0 9 * * *",
};

export const DAY_NAMES = [
  "Sun",
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
] as const;

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

export function validateCronExpression(cron: string): string | null {
  const trimmed = cron.trim();
  if (!trimmed) return "Enter a cron expression.";
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) {
    return "Use five cron fields: minute hour day month weekday.";
  }
  return null;
}

export function specToCron(spec: ScheduleSpec): string {
  const minute = clampNumber(spec.minute, 0, 59);
  const hour = clampNumber(spec.hour, 0, 23);
  const dayOfWeek = clampNumber(spec.dayOfWeek, 0, 6);
  const dayOfMonth = clampNumber(spec.dayOfMonth, 1, 28);

  switch (spec.frequency) {
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekly":
      return `${minute} ${hour} * * ${dayOfWeek}`;
    case "monthly":
      return `${minute} ${hour} ${dayOfMonth} * *`;
    case "custom":
      return spec.custom.trim();
  }
  return `${minute} ${hour} * * *`;
}

/** Parse a cron string back into a ScheduleSpec (best-effort). */
export function cronToSpec(cron: string): ScheduleSpec {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) {
    return { ...DEFAULT_SCHEDULE_SPEC, frequency: "custom", custom: cron };
  }
  const [min, hr, dom, month, dow] = parts;
  const minute = parseInt(min, 10);
  const hour = parseInt(hr, 10);

  if (
    month === "*" &&
    dom === "*" &&
    dow === "*" &&
    !isNaN(minute) &&
    !isNaN(hour)
  ) {
    return { ...DEFAULT_SCHEDULE_SPEC, frequency: "daily", hour, minute };
  }
  if (
    month === "*" &&
    dom === "*" &&
    !isNaN(parseInt(dow, 10)) &&
    !isNaN(minute) &&
    !isNaN(hour)
  ) {
    return {
      ...DEFAULT_SCHEDULE_SPEC,
      frequency: "weekly",
      hour: clampNumber(hour, 0, 23),
      minute: clampNumber(minute, 0, 59),
      dayOfWeek: clampNumber(parseInt(dow, 10), 0, 6),
    };
  }
  if (
    month === "*" &&
    dow === "*" &&
    !isNaN(parseInt(dom, 10)) &&
    !isNaN(minute) &&
    !isNaN(hour)
  ) {
    return {
      ...DEFAULT_SCHEDULE_SPEC,
      frequency: "monthly",
      hour: clampNumber(hour, 0, 23),
      minute: clampNumber(minute, 0, 59),
      dayOfMonth: clampNumber(parseInt(dom, 10), 1, 28),
    };
  }
  return { ...DEFAULT_SCHEDULE_SPEC, frequency: "custom", custom: cron };
}

/** Human-readable summary of a cron expression. */
export function cronLabel(cron: string): string {
  const spec = cronToSpec(cron);
  const time = `${String(spec.hour).padStart(2, "0")}:${String(
    spec.minute
  ).padStart(2, "0")}`;
  switch (spec.frequency) {
    case "daily":
      return `Every day at ${time}`;
    case "weekly":
      return `Every ${DAY_NAMES[spec.dayOfWeek]} at ${time}`;
    case "monthly":
      return `Monthly on day ${spec.dayOfMonth} at ${time}`;
    default:
      return cron;
  }
}

/** Format a next_run_date ISO string into a short relative label. */
export function nextRunLabel(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const now = Date.now();
  const diff = d.getTime() - now;
  if (diff < 0) return "Overdue";
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "in <1m";
  if (mins < 60) return `in ${mins}m`;
  const hrs = Math.round(diff / 3_600_000);
  if (hrs < 24) return `in ${hrs}h`;
  const days = Math.round(diff / 86_400_000);
  return `in ${days}d`;
}
