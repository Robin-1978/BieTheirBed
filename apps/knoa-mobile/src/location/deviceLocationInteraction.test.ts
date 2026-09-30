import { beforeEach, describe, expect, it, vi } from "vitest";

const location = vi.hoisted(() => ({
 preference: { enabled: true, precision: "block" as const },
 result: { text: "上海市浦东新区", precision: "block" as const },
}));

vi.mock("./deviceLocation", () => ({
 loadLocationPreference: vi.fn(async () => location.preference),
 resolveDeviceLocation: vi.fn(async () => location.result),
}));

import type { HumanInteraction } from "@/api/models";
import { answerDeviceLocationInteraction } from "./deviceLocationInteraction";

function request(overrides: Partial<HumanInteraction["display"]> = {}): HumanInteraction {
 return {
 interaction_id: "location-a",
 owner_kind: "conversation_turn",
 owner_id: "turn-a",
 kind: "device_location",
 state: "pending",
 display: {
 purpose: "nearby",
 precision: "block",
 address_required: true,
 ...overrides,
 },
 resolution_schema: {},
 resolution: null,
 created_at: 1,
 resolved_at: null,
 expires_at: null,
 };
}

beforeEach(() => {
 vi.clearAllMocks();
 location.preference = { enabled: true, precision: "block" };
 location.result = { text: "上海市浦东新区", precision: "block" };
});

describe("device location interaction", () => {
 it("resolves the phone location only after the Node requests it", async () => {
 const device = await import("./deviceLocation");

 await expect(answerDeviceLocationInteraction(request())).resolves.toEqual({
 status: "available",
 location: "上海市浦东新区",
 precision: "block",
 });
 expect(vi.mocked(device.resolveDeviceLocation)).toHaveBeenCalledWith({
 requestedPrecision: "block",
 addressRequired: true,
 });
 });

 it("returns disabled without touching location services", async () => {
 const device = await import("./deviceLocation");
 location.preference = { enabled: false, precision: "block" };

 await expect(answerDeviceLocationInteraction(request())).resolves.toEqual({
 status: "disabled",
 location: "",
 precision: "block",
 });
 expect(vi.mocked(device.resolveDeviceLocation)).not.toHaveBeenCalled();
 });

 it("rejects malformed requests without touching location services", async () => {
 const device = await import("./deviceLocation");

 await expect(answerDeviceLocationInteraction(request({ precision: "room" as never }))).resolves.toEqual({
 status: "unavailable",
 location: "",
 precision: "block",
 });
 expect(vi.mocked(device.resolveDeviceLocation)).not.toHaveBeenCalled();
 });
});
