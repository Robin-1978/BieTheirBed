declare module "react-native-zeroconf" {
  type ZeroconfService = {
    name: string;
    fullName?: string;
    host?: string;
    port: number;
    addresses?: string[];
    txt?: Record<string, string | number | boolean>;
  };

  export default class Zeroconf {
    on(event: "resolved" | "error" | "start" | "stop", listener: (service: ZeroconfService | Error | string) => void): void;
    removeListener?(event: string, listener: (...args: any[]) => void): void;
    scan(type?: string, protocol?: string, domain?: string, implType?: string): void;
    stop(implType?: string): void;
    getServices(): Record<string, ZeroconfService>;
  }
}
