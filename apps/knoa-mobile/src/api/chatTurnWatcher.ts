import type { ChatTurnSnapshot } from "./models";
import { subscribeChatTurn, type ChatTurnSubscription } from "./chatTurns";

type Connection = { gatewayUrl: string; token: string };
type Timer = ReturnType<typeof setTimeout>;

type Subscribe = (input: {
  gatewayUrl: string;
  token: string;
  turnId: string;
  onOpen(): void;
  onSnapshot(turn: ChatTurnSnapshot): void;
  onError(error: Error): void;
}) => ChatTurnSubscription;

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>([
  "completed",
  "failed",
  "cancelled",
]);

export class ChatTurnWatcher {
  private readonly active = new Map<string, ChatTurnSubscription>();
  private readonly timers = new Map<string, Timer>();
  private readonly failures = new Map<string, number>();
  private readonly generations = new Map<string, number>();
  private readonly watched = new Set<string>();

  constructor(private readonly input: {
    connection(): Connection | null;
    fetchSnapshot(turnId: string): Promise<ChatTurnSnapshot>;
    onSnapshot(turn: ChatTurnSnapshot): void;
    onUnavailable(turnId: string, error: Error): void;
    retryDelays?: readonly number[];
    subscribe?: Subscribe;
  }) {}

  watch(turnId: string): void {
    if (this.active.has(turnId) || this.timers.has(turnId)) return;
    this.watched.add(turnId);
    const generation = this.generations.get(turnId) ?? 0;
    this.open(turnId, generation);
  }

  close(turnId: string): void {
    this.generations.set(turnId, (this.generations.get(turnId) ?? 0) + 1);
    this.active.get(turnId)?.close();
    this.active.delete(turnId);
    const timer = this.timers.get(turnId);
    if (timer) clearTimeout(timer);
    this.timers.delete(turnId);
    this.failures.delete(turnId);
    this.watched.delete(turnId);
  }

  closeAll(): void {
    const turnIds = [...this.watched];
    for (const turnId of turnIds) this.close(turnId);
  }

  private open(turnId: string, generation: number): void {
    if (!this.isCurrent(turnId, generation)) return;
    const connection = this.input.connection();
    if (!connection) return;
    const subscribe = this.input.subscribe ?? subscribeChatTurn;
    const subscription = subscribe({
      ...connection,
      turnId,
      onOpen: () => {
        if (this.isCurrent(turnId, generation)) this.failures.delete(turnId);
      },
      onSnapshot: (snapshot) => {
        if (!this.isCurrent(turnId, generation)) return;
        this.failures.delete(turnId);
        this.input.onSnapshot(snapshot);
        if (TERMINAL_STATES.has(snapshot.state)) this.close(turnId);
      },
      onError: (error) => {
        if (!this.isCurrent(turnId, generation)) return;
        this.active.get(turnId)?.close();
        this.active.delete(turnId);
        void this.recover(turnId, generation, error);
      },
    });
    if (this.isCurrent(turnId, generation)) this.active.set(turnId, subscription);
    else subscription.close();
  }

  private async recover(turnId: string, generation: number, streamError: Error): Promise<void> {
    let snapshot: ChatTurnSnapshot;
    try {
      snapshot = await this.input.fetchSnapshot(turnId);
    } catch (error) {
      if (!this.isCurrent(turnId, generation)) return;
      const failures = (this.failures.get(turnId) ?? 0) + 1;
      this.failures.set(turnId, failures);
      const delays = this.retryDelays();
      if (failures > delays.length) {
        this.input.onUnavailable(
          turnId,
          error instanceof Error ? error : streamError,
        );
        this.close(turnId);
        return;
      }
      this.schedule(turnId, generation, delays[failures - 1] ?? delays.at(-1) ?? 1000);
      return;
    }
    if (!this.isCurrent(turnId, generation)) return;
    this.failures.delete(turnId);
    this.input.onSnapshot(snapshot);
    if (TERMINAL_STATES.has(snapshot.state)) {
      this.close(turnId);
      return;
    }
    this.schedule(turnId, generation, this.retryDelays()[0] ?? 750);
  }

  private schedule(turnId: string, generation: number, delay: number): void {
    if (!this.isCurrent(turnId, generation) || this.timers.has(turnId)) return;
    const timer = setTimeout(() => {
      this.timers.delete(turnId);
      this.open(turnId, generation);
    }, delay);
    this.timers.set(turnId, timer);
  }

  private retryDelays(): readonly number[] {
    const delays = this.input.retryDelays ?? [750, 1500, 3000];
    return delays.length ? delays : [750];
  }

  private isCurrent(turnId: string, generation: number): boolean {
    return this.watched.has(turnId) && (this.generations.get(turnId) ?? 0) === generation;
  }
}
