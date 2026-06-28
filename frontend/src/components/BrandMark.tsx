import { BRAND } from '../theme';

/**
 * The DataQ app glyph: a two-tone indigo yin-yang. The balance motif nods at
 * DataQ's pass/fail, expected/observed duality. Self-contained colours (dark +
 * light indigo) keep it legible on any light surface. Shared by the app header,
 * the page watermark, and the login page so the mark is defined once.
 */
export function BrandMark({ size = 30 }: { size?: number }) {
  const dark = BRAND.primary;
  const light = BRAND.primarySoft; // indigo-200
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="DataQ logo">
      <circle cx="50" cy="50" r="49" fill={light} stroke={BRAND.border} strokeWidth="1" />
      {/* The dark half: right lobe + the two interlocking teardrops. */}
      <path
        d="M50 1 a49 49 0 0 1 0 98 a24.5 24.5 0 0 1 0 -49 a24.5 24.5 0 0 0 0 -49 Z"
        fill={dark}
      />
      <circle cx="50" cy="25.5" r="9" fill={light} />
      <circle cx="50" cy="74.5" r="9" fill={dark} />
    </svg>
  );
}
