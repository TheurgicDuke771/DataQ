/**
 * Normalise an unknown thrown value to a user-facing string.
 *
 * Collapses the `instanceof Error` message-or-fallback ternary that recurred
 * across ~25 toast/catch sites into one place, so the fallback wording stays
 * consistent (and is trivially changeable).
 */
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'unknown error';
}
