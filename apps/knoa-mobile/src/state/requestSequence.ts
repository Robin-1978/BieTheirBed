export type RequestSequence = {
  /** Starts a request and returns its ticket; every earlier ticket is superseded. */
  begin(): number;
  /** True while no newer request has started and nothing was cancelled since. */
  isLatest(ticket: number): boolean;
  /** Supersedes every request that is still in flight. */
  cancel(): void;
};

/**
 * "Latest request wins" guard for user-triggered loads.  Responses may arrive
 * in any order; a response is applied only while its ticket is still the latest,
 * so tapping A then B can never leave A on screen, and a response that lands
 * after the panel was closed does not reopen it.
 */
export function createRequestSequence(): RequestSequence {
  let latest = 0;
  return {
    begin() {
      latest += 1;
      return latest;
    },
    isLatest(ticket) {
      return ticket === latest;
    },
    cancel() {
      latest += 1;
    },
  };
}
