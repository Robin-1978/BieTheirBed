import { createContext, useContext } from "react";
import type { ConnectionState } from "./types";

export const ConnectionContext = createContext<ConnectionState | null>(null);

export function useConnection(): ConnectionState {
  const value = useContext(ConnectionContext);
  if (!value) throw new Error("useConnection must be used within GatewayProvider");
  return value;
}
