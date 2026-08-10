import { GatewayError, type GatewayClient } from "../api/gatewayClient";

export async function withAuthenticationRetry<T>(
  client: GatewayClient,
  refresh: () => Promise<GatewayClient>,
  operation: (activeClient: GatewayClient) => Promise<T>,
): Promise<T> {
  try {
    return await operation(client);
  } catch (error) {
    if (!(error instanceof GatewayError) || error.status !== 401) throw error;
  }
  return operation(await refresh());
}
