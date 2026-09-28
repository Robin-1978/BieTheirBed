import { NativeModules, Platform } from "react-native";

export type NativeFix = {
  latitude: number;
  longitude: number;
  accuracy: number;
  provider: string;
  timestamp: number;
};

type KnoaLocationModule = {
  getFix(): Promise<NativeFix>;
};

function module(): KnoaLocationModule | null {
  if (Platform.OS !== "android") return null;
  const native = (NativeModules as Record<string, unknown>).KnoaLocation as
    | KnoaLocationModule
    | undefined;
  return native ?? null;
}

/**
 * GMS-independent fix from the platform LocationManager (cell + WiFi
 * network provider first, GPS fallback). Returns null when unavailable.
 * Never throws.
 */
export async function getNativeFix(): Promise<NativeFix | null> {
  const native = module();
  if (!native) return null;
  try {
    const fix = await native.getFix();
    if (!Number.isFinite(fix?.latitude) || !Number.isFinite(fix?.longitude)) return null;
    return fix;
  } catch {
    return null;
  }
}
