import type { HumanInteraction } from "@/api/models";
import {
 loadLocationPreference,
 resolveDeviceLocation,
 type LocationPrecision,
} from "./deviceLocation";

export type DeviceLocationResolution = {
 status: "available" | "disabled" | "unavailable";
 location: string;
 precision: LocationPrecision;
};

export async function answerDeviceLocationInteraction(
 interaction: HumanInteraction,
): Promise<DeviceLocationResolution> {
 const preference = await loadLocationPreference();
 const requestedPrecision = interaction.display.precision;
 const validPrecision =
  requestedPrecision === "city" ||
  requestedPrecision === "block" ||
  requestedPrecision === "precise"
   ? (requestedPrecision as LocationPrecision)
   : preference.precision;
 const fallback: DeviceLocationResolution = {
 status: "unavailable",
 location: "",
 precision: validPrecision,
 };
 if (interaction.kind !== "device_location" || interaction.state !== "pending") {
 return fallback;
 }
 const addressRequired = interaction.display.address_required;
 if (typeof addressRequired !== "boolean") {
 return fallback;
 }
 if (!preference.enabled) {
 return { ...fallback, status: "disabled" };
 }
 const snapshot = await resolveDeviceLocation({
 requestedPrecision: requestedPrecision as LocationPrecision,
 addressRequired,
 });
 if (!snapshot?.text) return fallback;
 return {
 status: "available",
 location: snapshot.text,
 precision: snapshot.precision,
 };
}
