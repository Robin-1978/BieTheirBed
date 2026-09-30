import * as Location from "expo-location";
import * as SecureStore from "expo-secure-store";

import { getNativeFix } from "./nativeLocation";

const LOCATION_PREFERENCE = "knoa.location.preference.v1";

export type LocationPrecision = "city" | "block" | "precise";

export type LocationPreference = {
 enabled: boolean;
 precision: LocationPrecision;
};

export type DeviceLocationRequest = {
 requestedPrecision: LocationPrecision;
 addressRequired: boolean;
};

export type DeviceLocationSnapshot = {
 text: string;
 precision: LocationPrecision;
};

const fallback: LocationPreference = { enabled: false, precision: "block" };

const MAX_DEVICE_LOCATION_CHARS = 500;
const FIX_TIMEOUT_MS = 8000;
/** Coordinate acquisition is bounded so a location tool call cannot hang indefinitely. */
const COORDINATE_BUDGET_MS = 8000;
const REVERSE_GEOCODE_BUDGET_MS = 3000;
const ON_DEMAND_CACHE_MS = 5 * 60 * 1000;

type Coords = { latitude: number; longitude: number; accuracy: number | null };
type CachedSnapshot = DeviceLocationSnapshot & {
 key: string;
 updatedAt: number;
};

let onDemandCache: CachedSnapshot | null = null;

/** First successful coords win; nulls keep waiting until budget or exhaustion. */
function firstCoords(sources: Array<Promise<Coords | null>>, budgetMs: number): Promise<Coords | null> {
 return new Promise((resolve) => {
  let done = false;
  let pending = sources.length;
  if (pending === 0) {
   resolve(null);
   return;
  }
  const timer = setTimeout(() => {
   if (!done) {
    done = true;
    resolve(null);
   }
  }, budgetMs);
  sources.forEach((source) => {
   source.then((coords) => {
    if (done) return;
    if (coords) {
     done = true;
     clearTimeout(timer);
     resolve(coords);
    } else if (--pending === 0) {
     done = true;
     clearTimeout(timer);
     resolve(null);
    }
   });
  });
 });
}

export async function loadLocationPreference(): Promise<LocationPreference> {
 const raw = await SecureStore.getItemAsync(LOCATION_PREFERENCE);
 if (!raw) return fallback;
 try {
  const value = JSON.parse(raw) as Partial<LocationPreference>;
  return {
   enabled: value.enabled === true,
   precision:
    value.precision === "city" || value.precision === "precise" ? value.precision : "block",
  };
 } catch {
  return fallback;
 }
}

export async function saveLocationPreference(next: LocationPreference): Promise<void> {
 onDemandCache = null;
 await SecureStore.setItemAsync(LOCATION_PREFERENCE, JSON.stringify(next));
}

/**
 * Best-effort position snapshot for an explicit Node location request.
 * No caller means the user's configured precision with a readable address.
 * Returns null when the switch is off, permission is denied, or any step
 * fails. It never runs in the background and never throws.
 */
export async function resolveDeviceLocation(
 request?: DeviceLocationRequest,
): Promise<DeviceLocationSnapshot | null> {
 try {
  const preference = await loadLocationPreference();
  if (!preference.enabled) return null;
  const requestedPrecision = request?.requestedPrecision ?? preference.precision;
  const addressRequired = request?.addressRequired ?? true;
  const precision = leastPrecise(preference.precision, requestedPrecision);
  const current = await Location.getForegroundPermissionsAsync();
  let granted = current.status === "granted";
  if (!granted && current.canAskAgain !== false) {
   const requested = await Location.requestForegroundPermissionsAsync();
   granted = requested.status === "granted";
  }
  if (!granted) return null;

  const cacheKey = `${precision}:${addressRequired ? "address" : "coordinates"}`;
  if (
   onDemandCache?.key === cacheKey
   && Date.now() - onDemandCache.updatedAt < ON_DEMAND_CACHE_MS
  ) {
   return { text: onDemandCache.text, precision: onDemandCache.precision };
  }

  // Indoor first fixes often exceed a few seconds: prefer the cached fix
  // when it is fresh, otherwise wait longer for a real GPS fix instead of
  // silently sending nothing.
  const wantedAccuracy =
   precision === "city"
    ? Location.LocationAccuracy.Low
    : precision === "block"
     ? Location.LocationAccuracy.Balanced
     : Location.LocationAccuracy.High;
  const fixTimeoutMs = precision === "precise" ? 20000 : FIX_TIMEOUT_MS;
  const expoCached: Promise<Coords | null> = (async () => {
   try {
    const cached = await withTimeout(Location.getLastKnownPositionAsync(), 2000);
    if (cached && Date.now() - cached.timestamp < 5 * 60 * 1000) return cached.coords;
   } catch {
    // Fall through to the other sources.
   }
   return null;
  })();
  const expoLive: Promise<Coords | null> = (async () => {
   try {
    const live = await withTimeout(
     Location.getCurrentPositionAsync({ accuracy: wantedAccuracy }),
     fixTimeoutMs,
    );
    return live.coords;
   } catch {
    return null;
   }
  })();
  const native: Promise<Coords | null> = (async () => {
   // GMS-less ROMs (e.g. Honor MagicOS China builds) have no fused
   // provider: the platform LocationManager network fix is usually first.
   const fix = await getNativeFix();
   if (fix && Date.now() - fix.timestamp < 5 * 60 * 1000) {
    return { latitude: fix.latitude, longitude: fix.longitude, accuracy: fix.accuracy };
   }
   return null;
  })();
  const fix = await firstCoords([expoCached, native, expoLive], COORDINATE_BUDGET_MS);
  if (!fix) return null;
  const { latitude, longitude, accuracy } = fix;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;

  const placemarks = addressRequired
   ? await withTimeout(
    Location.reverseGeocodeAsync({ latitude, longitude }),
    REVERSE_GEOCODE_BUDGET_MS,
   ).catch(() => [] as Location.LocationGeocodedAddress[])
   : [] as Location.LocationGeocodedAddress[];
  const [place] = placemarks;
  const city = compact([place?.city, place?.district]);
  const block = compact([place?.city, place?.district, place?.street, place?.name]);
  const coordinateDigits = precision === "city" ? 1 : precision === "block" ? 3 : 4;
  const coords =
   `(${latitude.toFixed(coordinateDigits)},${longitude.toFixed(coordinateDigits)}` +
   (typeof accuracy === "number" && Number.isFinite(accuracy) ? ` ±${Math.round(accuracy)}m` : "") +
   ")";
  let text = "";
  if (precision === "city") {
   text = city || block || coords;
  } else if (precision === "block") {
   text = block || city || coords;
  } else if (!addressRequired) {
   text = coords;
  } else {
   text = `${block || city || "未知位置"} ${coords}`;
  }
  const snapshot = {
   text: text.trim().slice(0, MAX_DEVICE_LOCATION_CHARS),
   precision,
  };
  onDemandCache = { ...snapshot, key: cacheKey, updatedAt: Date.now() };
  return snapshot;
 } catch {
  return null;
 }
}

function leastPrecise(
 allowed: LocationPrecision,
 requested: LocationPrecision,
): LocationPrecision {
 const ranks: Record<LocationPrecision, number> = { city: 0, block: 1, precise: 2 };
 return ranks[allowed] <= ranks[requested] ? allowed : requested;
}

function compact(parts: Array<string | null | undefined>): string {
 const seen = new Set<string>();
 const out: string[] = [];
 for (const part of parts) {
  const text = (part ?? "").trim();
  if (!text || seen.has(text)) continue;
  seen.add(text);
  out.push(text);
 }
 return out.join("");
}

async function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
 let timer: ReturnType<typeof setTimeout> | undefined;
 try {
  return await Promise.race([
   promise,
   new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("location_timeout")), ms);
   }),
  ]);
 } finally {
  if (timer !== undefined) clearTimeout(timer);
 }
}
