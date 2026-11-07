/** Smallest magnitude shown as a number; anything non-zero below it reads `<0.001`, never `0`. */
const FLOOR = 0.001;

/**
 * A reader-facing number: integers stay integers, other values keep `sig` significant figures
 * (`0.05875440658049354` -> `0.0588`), and a non-zero value too small to show reads `<0.001`
 * rather than a misleading `0`. The full value belongs in a title/tooltip (see `fullNumber`).
 */
export function formatSig(value: number, sig = 3): string {
  if (!Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return String(value);
  const abs = Math.abs(value);
  if (abs < FLOOR) return value < 0 ? `>-${FLOOR}` : `<${FLOOR}`;
  if (abs >= 10 ** sig) return String(Math.round(value));
  return String(Number(value.toPrecision(sig)));
}

/** The exact value, for a tooltip or accessible label beside a `formatSig` rendering. */
export function fullNumber(value: number): string {
  return String(value);
}

const COMPACT = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumSignificantDigits: 3,
});

/** Chart axis tick: compact (`2.1K`, `0.06`) so the label stays narrow. */
export function formatTick(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  return COMPACT.format(value);
}
