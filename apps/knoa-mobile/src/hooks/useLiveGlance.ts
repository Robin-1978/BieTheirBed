import { useCallback, useState } from "react";

import type { GatewayClient } from "@/api/gatewayClient";
import type { DesktopGlanceRecord } from "@/api/models";

export function useLiveGlance({
  hasClient,
  runAuthenticated,
}: {
  hasClient: boolean;
  runAuthenticated: <T>(operation: (client: GatewayClient) => Promise<T>) => Promise<T>;
}) {
  const [glance, setGlance] = useState<DesktopGlanceRecord | null>(null);
  const [visible, setVisible] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const open = useCallback(async () => {
    if (!hasClient) return;
    setRefreshing(true);
    setVisible(true);
    try {
      const record = await runAuthenticated((client) => client.getLiveDesktopGlance());
      if (record) setGlance(record);
    } finally {
      setRefreshing(false);
    }
  }, [hasClient, runAuthenticated]);

  const refresh = useCallback(async () => {
    if (!hasClient || refreshing) return;
    setRefreshing(true);
    try {
      const record = await runAuthenticated((client) => client.getLiveDesktopGlance());
      if (record) setGlance(record);
    } finally {
      setRefreshing(false);
    }
  }, [hasClient, refreshing, runAuthenticated]);

  const close = useCallback(() => setVisible(false), []);

  return { glance, visible, refreshing, open, refresh, close };
}
