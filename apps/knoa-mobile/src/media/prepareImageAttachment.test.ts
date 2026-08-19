import { describe, expect, it } from "vitest";

import { boundedDimensions, MAX_CHAT_IMAGE_EDGE } from "./imageBounds";

describe("boundedDimensions", () => {
  it("keeps small images at their original size", () => {
    expect(boundedDimensions(1200, 900)).toEqual({ width: 1200, height: 900 });
  });

  it("reduces phone photos while preserving orientation", () => {
    expect(boundedDimensions(8064, 6048)).toEqual({ width: MAX_CHAT_IMAGE_EDGE, height: 1200 });
    expect(boundedDimensions(3024, 4032)).toEqual({ width: 1200, height: MAX_CHAT_IMAGE_EDGE });
  });

  it("rejects invalid dimensions", () => {
    expect(() => boundedDimensions(0, 100)).toThrow("图片尺寸无效");
  });
});
