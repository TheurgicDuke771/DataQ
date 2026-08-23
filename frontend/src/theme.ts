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

/** Dark-mode counterpart of BRAND (#1562). */
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
  const surfaceBg = mode === 'dark' ? '#161b22' : '#ffffff';
  const tableHeaderBg = mode === 'dark' ? '#161b22' : '#fafbfc';

  return {
    algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: brand.primary,
      colorInfo: brand.primary,
      colorLink: brand.primary,
      colorTextHeading: brand.ink,
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
        bodyBg: brand.canvas,
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
      Table: {
        headerBg: tableHeaderBg,
      },
    },
  };
}
