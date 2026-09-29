import { describe, expect, it } from "vitest";

import { GatewayError } from "../../api/gatewayClient";
import { describeSendError } from "./types";

const t = ((key: string) => key) as (key: string) => string;

describe("describeSendError", () => {
  it("marks 400 rejections as non-retryable with the version hint", () => {
    const result = describeSendError(new GatewayError(400, "invalid_request"), t);
    expect(result).toEqual({ message: "chat.sendRejectedVersion", retryable: false });
  });

  it("marks 422 rejections as non-retryable with the version hint", () => {
    const result = describeSendError(new GatewayError(422, "rejected"), t);
    expect(result).toEqual({ message: "chat.sendRejectedVersion", retryable: false });
  });

  it("marks 404 as non-retryable while keeping the server message", () => {
    const error = new GatewayError(404, "not_found");
    const result = describeSendError(error, t);
    expect(result.retryable).toBe(false);
    expect(result.message).toBe(error.message);
  });

  it("keeps transient failures retryable with the server message", () => {
    const error = new GatewayError(503, "unavailable");
    const result = describeSendError(error, t);
    expect(result).toEqual({ message: error.message, retryable: true });
  });

  it("falls back to the generic message for non-gateway errors", () => {
    expect(describeSendError(new Error("boom"), t)).toEqual({
      message: "chat.sendFailed",
      retryable: true,
    });
  });
});
