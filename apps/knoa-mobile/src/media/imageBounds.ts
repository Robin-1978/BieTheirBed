// Phone photos are reduced before upload. 1024px is intentionally conservative:
// it keeps text legible while avoiding excessive vision tokens and memory use on
// small local models such as a 4B llama.cpp deployment.
export const MAX_CHAT_IMAGE_EDGE = 1024;

export function boundedDimensions(width: number, height: number): { width: number; height: number } {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error("图片尺寸无效");
  }
  const scale = Math.min(1, MAX_CHAT_IMAGE_EDGE / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}
