import type { ThemeConfig } from 'antd';

/**
 * App-wide Ant Design theme. Kept in one module so the brand palette and the
 * shell (App.tsx) read from the same tokens rather than hard-coding hexes.
 *
 * Design intent: move off the stock antd "navy-header admin template" look.
 * An indigo primary + a soft gray layout canvas (`colorBgLayout`) give the
 * white surfaces (header, sider, cards) depth and definition — so the page no
 * longer reads as a flat sea of white, and the empty space frames content
 * instead of just being blank.
 */

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

/** Shared shell metrics so App.tsx and the theme agree. */
export const SHELL = {
  headerHeight: 56,
  siderWidth: 220,
} as const;

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorLink: BRAND.primary,
    colorTextHeading: BRAND.ink,
    colorBgLayout: BRAND.canvas,
    borderRadius: 8,
    fontFamily:
      "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    fontSize: 14,
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      headerHeight: SHELL.headerHeight,
      headerPadding: '0 24px',
      siderBg: '#ffffff',
      bodyBg: BRAND.canvas,
    },
    Menu: {
      // Rounded, inset nav items read as a modern sidebar rather than full-bleed rows.
      itemBorderRadius: 8,
      itemMarginInline: 8,
      itemHeight: 38,
      itemSelectedBg: BRAND.selectedBg,
      itemSelectedColor: BRAND.primary,
    },
    Card: {
      borderRadiusLG: 12,
    },
    Table: {
      headerBg: '#fafbfc',
    },
  },
};
