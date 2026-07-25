/**
 * "This credential is about to die" — one definition, every credential (#838).
 *
 * A connection's SAS and a DataQ PAT expire the same way and deserve the same
 * warning, so the window and the wording live here rather than being re-derived
 * per panel. Prod lineage was dark for six days behind an expired SAS; the point
 * of this module is that the product says so *before* something breaks.
 *
 * The threshold is the FRONTEND's, deliberately not the backend's: the API hands
 * over a date, not a verdict, so how loudly to warn is a presentation decision
 * that can change without redeploying the worker.
 */

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

/**
 * Classify an ISO expiry timestamp against the warning window.
 *
 * `null`/absent/unparseable all collapse to `unknown` — a credential we cannot
 * read the lifetime of must never be shown as healthy on the strength of a
 * missing field.
 *
 * `daysLeft` is rounded UP so a credential with 18 hours left reads "1 day",
 * not "0 days": the number is a deadline, and rounding a deadline down makes it
 * look like it has already passed.
 */
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
