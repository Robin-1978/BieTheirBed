export type CursorStore = {
  load(): Promise<number>;
  save(value: number): Promise<void>;
};

export class EventCursor {
  private value = 0;

  constructor(private readonly store: CursorStore) {}

  async initialize(): Promise<number> {
    this.value = Math.max(0, await this.store.load());
    return this.value;
  }

  current(): number {
    return this.value;
  }

  async accept(eventId: number): Promise<boolean> {
    if (!Number.isSafeInteger(eventId) || eventId <= this.value) return false;
    this.value = eventId;
    await this.store.save(eventId);
    return true;
  }
}
