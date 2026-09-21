import { theme as antdTheme, type ThemeConfig } from 'antd';

/** App-wide Ant Design theme. */

export const BRAND = {
  /** Indigo-600 — the primary accent (buttons, active nav, links). */
  primary: '#4f46e5',
  /** Indigo-200 — the logo's light lobe + the watermark tint. */
  primarySoft: '#c7d2fe',
  /** Pale indigo — the "selected" row/nav background (one tint everywhere). */
  selectedBg: '#eef0fe',
  /** The soft canvas behind white surfaces. */
  canvas: '#f4f5f7',
  /** Hairline border for header / sider / cards. */
  border: '#e6e8eb',
  /** Primary text. */
  ink: '#1f2430',
} as const;

/**
 * Dark counterpart of BRAND — must match styles.css's `:root[data-theme='dark']`.
 * Duplicated rather than read from CSS: antd's `algorithm` derives hover/active
 * shades from token.colorPrimary/colorBgLayout at JS time and can't do that math
 * on a `var(...)` string.
 */
export const DARK_BRAND = {
  primary: '#818cf8',
  primarySoft: '#3730a3',
  selectedBg: 'rgba(129, 140, 248, 0.16)',
  canvas: '#0d1117',
  border: '#30363d',
  ink: '#e6edf3',
} as const;

/** Shared shell metrics so App.tsx and the theme agree. */
export const SHELL = {
  headerHeight: 56,
  siderWidth: 220,
} as const;

/**
 * Shared good/warning/bad/neutral scale — antd's green-6/gold-6/red-6/gray-5,
 * defined as CSS vars in styles.css (values intentionally unchanged across
 * themes — already legible against both the light and dark canvas).
 */
export const SEVERITY_SCALE = {
  good: 'var(--dq-severity-good)',
  warning: 'var(--dq-severity-warning)',
  bad: 'var(--dq-severity-bad)',
  neutral: 'var(--dq-severity-neutral)',
} as const;

export type AppThemeMode = 'light' | 'dark';

export function getAppTheme(mode: AppThemeMode): ThemeConfig {
  const brand = mode === 'dark' ? DARK_BRAND : BRAND;
  // Plain pass-through overrides (unlike token.colorPrimary/colorBgLayout below,
  // which the algorithm derives shades from) — safe to read the CSS var directly.
  const surfaceBg = 'var(--dq-surface)';
  const tableHeaderBg = 'var(--dq-surface)';

  return {
    algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: brand.primary,
      colorInfo: brand.primary,
      // The dark algorithm dims the indigo to #717ad6 for links — 4.36:1 on the dark surface.
      colorLink: mode === 'dark' ? '#a5b4fc' : brand.primary,
      // Derived hover/active land at 2.6 / 4.3:1 on the dark surface.
      ...(mode === 'dark' ? { colorLinkHover: '#c7d2fe', colorLinkActive: '#a5b4fc' } : {}),
      colorTextHeading: brand.ink,
      // red-6 danger text is 3.3:1 on white and, after the dark algorithm, 4.3:1 on dark.
      colorError: mode === 'dark' ? '#ff7875' : '#cf1322',
      // antd's 0.45 default is 3.4:1 on white and 4.48:1 on the dark surface.
      colorTextDescription: mode === 'dark' ? 'rgba(255, 255, 255, 0.6)' : 'rgba(0, 0, 0, 0.6)',
      // Same 0.45 default: Descriptions labels, Statistic titles, table sorter captions.
      colorTextTertiary: mode === 'dark' ? 'rgba(255, 255, 255, 0.6)' : 'rgba(0, 0, 0, 0.6)',
      // 0.25 default is 1.8:1. Still visibly lighter than a 0.88 value.
      colorTextPlaceholder: mode === 'dark' ? 'rgba(255, 255, 255, 0.5)' : 'rgba(0, 0, 0, 0.55)',
      colorBgLayout: brand.canvas,
      borderRadius: 8,
      fontFamily:
        "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
      // Code / SQL / identifiers — JetBrains Mono (self-hosted via @fontsource),
      // falling back to the platform monospace stack.
      fontFamilyCode:
        "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
      fontSize: 14,
    },
    components: {
      Layout: {
        headerBg: surfaceBg,
        headerHeight: SHELL.headerHeight,
        headerPadding: '0 24px',
        siderBg: surfaceBg,
        bodyBg: 'var(--dq-canvas)',
      },
      Menu: {
        // Rounded, inset nav items read as a modern sidebar rather than full-bleed rows.
        itemBorderRadius: 8,
        itemMarginInline: 8,
        itemHeight: 38,
        itemSelectedBg: brand.selectedBg,
        itemSelectedColor: brand.primary,
      },
      Card: {
        borderRadiusLG: 12,
      },
      // White on the dark theme's lighter indigo is 2.98:1. Scoped per component: the global
      // colorTextLightSolid is also Tooltip's text, which sits on a dark surface.
      ...(mode === 'dark'
        ? {
            Button: { primaryColor: DARK_BRAND.canvas, dangerColor: DARK_BRAND.canvas },
            Avatar: { colorTextLightSolid: DARK_BRAND.canvas },
          }
        : {}),
      Table: {
        headerBg: tableHeaderBg,
      },
    },
  };
}
