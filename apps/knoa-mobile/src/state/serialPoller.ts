export type SerialPoller = {
  start(): void;
  stop(): void;
};

/**
 * Runs `run` repeatedly, waiting `intervalMs` after each run *finishes*.
 *
 * Unlike `setInterval`, a slow run can never overlap the next one, and a
 * rejected run does not end the schedule (`run` is expected to report its own
 * failures).  The first run starts immediately and receives attempt 0, so
 * callers can show a loading state only for it.
 */
export function createSerialPoller(options: {
  intervalMs: number;
  run(attempt: number): Promise<void>;
}): SerialPoller {
  let active = false;
  // Bumped on every start/stop so that a run still in flight from an earlier
  // start can never revive a schedule that was cancelled or restarted.
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async (current: number, attempt: number): Promise<void> => {
    timer = null;
    try {
      await options.run(attempt);
    } catch {
      // One failed run must not end polling.
    } finally {
      if (active && generation === current) {
        timer = setTimeout(() => void tick(current, attempt + 1), options.intervalMs);
      }
    }
  };

  return {
    start() {
      if (active) return;
      active = true;
      generation += 1;
      void tick(generation, 0);
    },
    stop() {
      active = false;
      generation += 1;
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}
