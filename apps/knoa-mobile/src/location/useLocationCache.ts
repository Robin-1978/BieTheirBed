import { useEffect } from "react";
import { AppState } from "react-native";

import { refreshLocationCache, startLocationCache } from "./locationCache";

/**
 * Warms the location cache on launch, refreshes it every few minutes and
 * on every foreground. The send path only reads the cache, never blocks.
 */
export function useLocationCache() {
  useEffect(() => {
    const stop = startLocationCache();
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") void refreshLocationCache();
    });
    return () => {
      subscription.remove();
      stop();
    };
  }, []);
}
