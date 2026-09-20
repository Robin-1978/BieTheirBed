/** Enrollment Code 展示：短码对账 + 倒计时（纯函数，可测）。 */

export function enrollmentShortCode(grantId: string): string {
  const clean = grantId.replace(/[^0-9a-z]/gi, "").toUpperCase();
  const head = clean.slice(0, 8).padEnd(4, "•");
  return head.length > 4 ? `${head.slice(0, 4)}-${head.slice(4)}` : head;
}

export function countdownSeconds(expiresAt: number, nowSec: number): number {
  return Math.max(0, Math.floor(expiresAt - nowSec));
}

export function formatCountdown(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
