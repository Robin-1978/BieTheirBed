// Cache identity is kept outside individual stores so every cache uses the
// same account/node boundary.  It is intentionally process-local: the
// GatewayProvider sets it after restoring the secure identity and clears it
// when the user disconnects.  Old unscoped snapshots become unreadable rather
// than leaking into another account.
let activeIdentity = "";

export function setCacheIdentity(identity: string): void {
  activeIdentity = identity.trim();
}

export function scopedCacheKey(scope: string): string {
  const normalized = scope.trim();
  return activeIdentity ? `${activeIdentity}:${normalized}` : normalized;
}
