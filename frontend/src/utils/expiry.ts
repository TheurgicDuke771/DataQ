/** "This credential is about to die" — one definition, every credential (#838). */

/** How far ahead a credential counts as "expiring soon". */
export const CREDENTIAL_EXPIRY_WARN_DAYS = 14;

const MS_PER_DAY = 86_400_000;

export type ExpiryStatus =
  /** No readable expiry — the credential type has none, or it was never read.
   *  NOT the same as "never expires": render it as silence, never reassurance. */
  | { kind: 'unknown' }
  | { kind: 'ok'; daysLeft: number }
  | { kind: 'expiring'; daysLeft: number }
  | { kind: 'expired'; daysLeft: number };

/** Classify an ISO expiry timestamp against the warning window. */
export function expiryStatus(
  expiresAt: string | null | undefined,
  now: number = Date.now(),
): ExpiryStatus {
  if (!expiresAt) return { kind: 'unknown' };
  const at = new Date(expiresAt).getTime();
  if (Number.isNaN(at)) return { kind: 'unknown' };

  const daysLeft = Math.ceil((at - now) / MS_PER_DAY);
  if (at <= now) return { kind: 'expired', daysLeft };
  if (daysLeft <= CREDENTIAL_EXPIRY_WARN_DAYS) return { kind: 'expiring', daysLeft };
  return { kind: 'ok', daysLeft };
}

/** Badge text for a credential worth warning about (`expiring` / `expired`). */
export function expiryLabel(status: ExpiryStatus): string | null {
  switch (status.kind) {
    case 'expired':
      return 'credential expired';
    case 'expiring':
      return `credential expires in ${status.daysLeft}d`;
    default:
      return null;
  }
}
