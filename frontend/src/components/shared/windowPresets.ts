/**
 * Trailing-window presets shared between the Results date filter (client-side, via
 * `isWithinWindowDays`) and the Dashboard range selector (server-side, via the summary endpoint's
 */
export const WINDOW_PRESETS = [
  { value: '1', days: 1, label: 'Last 24h' },
  { value: '7', days: 7, label: 'Last 7 days' },
  { value: '30', days: 30, label: 'Last 30 days' },
] as const;

export type WindowPresetValue = (typeof WINDOW_PRESETS)[number]['value'];
