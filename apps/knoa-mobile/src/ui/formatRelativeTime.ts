export function formatRelativeTime(value: number, locale: string): string {
  const elapsed = Math.max(0, Date.now() - value);
  if (elapsed < 60_000) return locale.startsWith("zh") ? "刚刚" : "just now";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 60) return locale.startsWith("zh") ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return locale.startsWith("zh") ? `${hours} 小时前` : `${hours}h ago`;
  return new Date(value).toLocaleDateString(locale);
}
