export interface GatewayTransport {
  request(baseUrl: string, path: string, init: RequestInit): Promise<Response>;
  mode(): "direct" | "p2p" | "relay";
  close?(): void;
}

export class DirectFetchTransport implements GatewayTransport {
  mode(): "direct" {
    return "direct";
  }

  request(baseUrl: string, path: string, init: RequestInit): Promise<Response> {
    return fetch(`${baseUrl.replace(/\/$/, "")}${path}`, init);
  }
}
