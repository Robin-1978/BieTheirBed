import { describe, expect, it } from "vitest";

import { isUtf8TextContentType, relayResponseBody } from "./relayResponse";

const encoder = new TextEncoder();

describe("Relay response body reconstruction", () => {
  it.each([
    "application/json",
    "application/json; charset=utf-8",
    "application/problem+json",
    "application/x-ndjson",
    "text/event-stream",
    "text/plain; charset=UTF-8",
  ])("decodes %s explicitly as UTF-8", (contentType) => {
    const value = JSON.stringify({ title: "分析新分配工单", result: "繁體中文🙂e\u0301" });
    const body = relayResponseBody(encoder.encode(value), { "Content-Type": contentType });

    expect(typeof body).toBe("string");
    expect(JSON.parse(body as string)).toEqual({
      title: "分析新分配工单",
      result: "繁體中文🙂e\u0301",
    });
  });

  it("decodes a multibyte character after its Relay chunks are reassembled", () => {
    const encoded = encoder.encode("开始🙂完成");
    const splitInsideEmoji = 8;
    const first = encoded.slice(0, splitInsideEmoji);
    const second = encoded.slice(splitInsideEmoji);
    const reassembled = new Uint8Array(first.length + second.length);
    reassembled.set(first, 0);
    reassembled.set(second, first.length);

    expect(relayResponseBody(reassembled, { "content-type": "text/plain" }))
      .toBe("开始🙂完成");
  });

  it("preserves binary Artifact bytes without text conversion", () => {
    const bytes = Uint8Array.from([0, 255, 195, 40, 128, 1]);
    const body = relayResponseBody(bytes, { "content-type": "application/octet-stream" });

    expect(body).toBeInstanceOf(ArrayBuffer);
    expect([...new Uint8Array(body as ArrayBuffer)]).toEqual([...bytes]);
  });

  it("does not guess that an unknown application type is text", () => {
    expect(isUtf8TextContentType("application/zip")).toBe(false);
    expect(isUtf8TextContentType("image/png")).toBe(false);
  });
});
