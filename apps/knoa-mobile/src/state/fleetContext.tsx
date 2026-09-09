import { createContext, useContext } from "react";
import type { FleetState } from "./types";

export const FleetContext = createContext<FleetState | null>(null);

export function useFleet(): FleetState {
  const value = useContext(FleetContext);
  if (!value) throw new Error("useFleet must be used within GatewayProvider");
  return value;
}
