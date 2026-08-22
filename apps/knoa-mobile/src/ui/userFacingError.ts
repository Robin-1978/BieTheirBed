/** Convert transport/runtime failures into short, actionable user copy. */
export function userFacingError(error: unknown, fallback: string): string {
  const message = error instanceof Error
    ? error.message.trim()
    : typeof error === "object" && error !== null && typeof (error as { message?: unknown }).message === "string"
      ? String((error as { message: string }).message).trim()
      : "";
  if (!message) return fallback;
  const kind = typeof error === "object" && error !== null && "kind" in error
    ? String((error as { kind?: unknown }).kind ?? "") : "";
  if (kind === "timeout" || /timeout|timed out|超时/i.test(message)) return "连接超时，请检查网络后重试";
  if (kind === "cancelled" || /abort|cancel|取消/i.test(message)) return "操作已取消";
  if (kind === "network" || /network|fetch|offline|网络|连接失败/i.test(message)) return "暂时无法连接，请检查网络后重试";
  if (/401|403|unauthori[sz]ed|认证|登录已过期/i.test(message)) return "登录状态已失效，请重新登录";
  if (/[\\/](?:home|Users|ProgramData|workspace|venv)[\\/]|Traceback| at .+\(/i.test(message)) return fallback;
  return message.length > 180 ? `${message.slice(0, 177)}...` : message;
}
