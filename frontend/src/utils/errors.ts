/**
 * Normalise an unknown thrown value to a user-facing string.
 *
 * Collapses the `instanceof Error` message-or-fallback ternary that recurred
 * across the toast/catch sites into one place. The default `'unknown error'`
 * fallback suits user-facing toasts; the fetch-error sites that want the raw
 * `String(err)` for a non-Error throw pass it explicitly.
 */
export function errorMessage(err: unknown, fallback = 'unknown error'): string {
  return err instanceof Error ? err.message : fallback;
}
