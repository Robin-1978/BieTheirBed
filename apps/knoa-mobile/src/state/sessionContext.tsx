import { createContext, useContext } from "react";
import type { SessionState } from "./types";

export const SessionContext = createContext<SessionState | null>(null);

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used within GatewayProvider");
  return value;
}
