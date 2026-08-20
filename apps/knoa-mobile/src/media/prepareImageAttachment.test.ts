import { describe, expect, it } from "vitest";

import { boundedDimensions, MAX_CHAT_IMAGE_EDGE } from "./imageBounds";

describe("boundedDimensions", () => {
  it("keeps images within the safe edge at their original size", () => {
    expect(boundedDimensions(800, 600)).toEqual({ width: 800, height: 600 });
  });

  it("reduces phone photos while preserving orientation", () => {
    expect(boundedDimensions(8064, 6048)).toEqual({ width: MAX_CHAT_IMAGE_EDGE, height: 768 });
    expect(boundedDimensions(3024, 4032)).toEqual({ width: 768, height: MAX_CHAT_IMAGE_EDGE });
  });

  it("rejects invalid dimensions", () => {
    expect(() => boundedDimensions(0, 100)).toThrow("图片尺寸无效");
  });
});
