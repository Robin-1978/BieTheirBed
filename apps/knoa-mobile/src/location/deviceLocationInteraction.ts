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
 const fallback: DeviceLocationResolution = {
 status: "unavailable",
 location: "",
 precision: preference.precision,
 };
 if (interaction.kind !== "device_location" || interaction.state !== "pending") {
 return fallback;
 }
 const requestedPrecision = interaction.display.precision;
 const addressRequired = interaction.display.address_required;
 if (
 !["city", "block", "precise"].includes(requestedPrecision ?? "")
 || typeof addressRequired !== "boolean"
 ) {
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
