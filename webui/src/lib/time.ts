export function formatTime(date: Date, now = new Date()): string {
  if (!Number.isFinite(date.getTime())) return "—";

  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days <= 0) {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date);
  }
  if (days === 1) return "昨天";
  if (days < 7) {
    return new Intl.DateTimeFormat("zh-CN", { weekday: "long" }).format(date);
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatFullTime(date: Date): string {
  if (!Number.isFinite(date.getTime())) return "未知时间";

  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    hourCycle: "h23",
  }).format(date);
}
