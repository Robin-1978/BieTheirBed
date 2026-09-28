import * as SecureStore from "expo-secure-store";

import { loadLocationPreference, resolveDeviceLocation } from "./deviceLocation";

const LOCATION_CACHE_KEY = "knoa.location.cache.v1";
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

export type LocationCache = {
  text: string;
  updatedAt: number;
};

let memory: LocationCache | null = null;
let refreshing = false;

/** Fast, non-blocking read for the send path. Never定位, never throws. */
export async function getCachedLocationText(): Promise<string> {
  if (memory) return memory.text;
  try {
    const raw = await SecureStore.getItemAsync(LOCATION_CACHE_KEY);
    if (!raw) return "";
    const parsed = JSON.parse(raw) as Partial<LocationCache>;
    if (typeof parsed.text !== "string" || typeof parsed.updatedAt !== "number") return "";
    memory = { text: parsed.text, updatedAt: parsed.updatedAt };
    return memory.text;
  } catch {
    return "";
  }
}

/**
 * Background refresh: full GPS-grade snapshot, then cached for sends.
 * Empty results never poison a previous good fix. Never throws.
 */
export async function refreshLocationCache(): Promise<void> {
  if (refreshing) return;
  refreshing = true;
  try {
    if (!(await loadLocationPreference()).enabled) return;
    const text = await resolveDeviceLocation();
    if (!text) return;
    memory = { text, updatedAt: Date.now() };
    await SecureStore.setItemAsync(LOCATION_CACHE_KEY, JSON.stringify(memory));
  } catch {
    // Keep the previous fix.
  } finally {
    refreshing = false;
  }
}

/** Warm on launch and re-arm every interval. Returns a stop function. */
export function startLocationCache(): () => void {
  let stopped = false;
  void refreshLocationCache();
  const timer = setInterval(() => {
    if (!stopped) void refreshLocationCache();
  }, REFRESH_INTERVAL_MS);
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
