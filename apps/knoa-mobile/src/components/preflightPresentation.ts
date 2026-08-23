import type { TaskPreflightCheck } from "@/api/models";

/** Present execution preflight facts without exposing local runtime internals. */
export function preflightCheckMessage(check: TaskPreflightCheck): string {
  const detail = check.detail.trim();
  if (/runtime|codex|llama|vision|mcp/i.test(check.check_id) && /[\\/](?:home|Users|ProgramData|venv|workspace)[\\/]/i.test(detail)) {
    return "执行环境尚未准备好，请在 Node Console 检查运行能力";
  }
  if (/directory|working|workspace|目录|工作目录/i.test(detail) && /不存在|not found|no such/i.test(detail)) {
    return "工作目录不存在，请在 Node Console 修复工作目录后重试";
  }
  if (!detail) return check.status === "blocked" ? "执行前检查未通过" : "执行前检查需要关注";
  return detail.length > 180 ? `${detail.slice(0, 177)}...` : detail;
}

export function blockedPreflightMessages(checks: TaskPreflightCheck[]): string[] {
  return checks.filter((check) => check.status === "blocked").map(preflightCheckMessage);
}

/** Warnings do not block execution, but the user should acknowledge them first. */
export function warningPreflightMessages(checks: TaskPreflightCheck[]): string[] {
  return checks.filter((check) => check.status === "warning").map(preflightCheckMessage);
}
