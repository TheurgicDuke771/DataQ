/** The DataQ app glyph: a two-tone indigo yin-yang. */
export function BrandMark({ size = 30 }: { size?: number }) {
  const dark = 'var(--dq-primary)';
  const light = 'var(--dq-primary-soft)'; // indigo-200
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="DataQ logo">
      <circle cx="50" cy="50" r="49" fill={light} stroke={'var(--dq-border)'} strokeWidth="1" />
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
