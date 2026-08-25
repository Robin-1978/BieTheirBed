export function formatRelativeTime(value: number, locale: string): string {
  const elapsed = Math.max(0, Date.now() - value);
  if (elapsed < 60_000) return locale.startsWith("zh") ? "刚刚" : "just now";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 60) return locale.startsWith("zh") ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return locale.startsWith("zh") ? `${hours} 小时前` : `${hours}h ago`;
  return new Date(value).toLocaleDateString(locale);
}

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

export function formatMessageTimestamp(timestampMs: number, locale: string, yesterdayLabel: string): string {
  const date = new Date(timestampMs);
  const now = new Date();
  const time = date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hour12: false });
  const dayDiff = Math.floor((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (dayDiff === 0) return time;
  if (dayDiff === 1) return yesterdayLabel.replace("{time}", time);
  if (date.getFullYear() === now.getFullYear()) {
    const datePart = date.toLocaleDateString(locale, { month: "short", day: "numeric" });
    return `${datePart} ${time}`;
  }
  return date.toLocaleString(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
