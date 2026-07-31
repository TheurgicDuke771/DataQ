/**
 * Trailing-window presets shared between the Results date filter (client-side,
 * via `isWithinWindowDays`) and the Dashboard range selector (server-side,
 * via the summary endpoint's `days` param). The two surfaces used to define
 * their own 24h/7d/30d lists (`DATE_WINDOWS` / `RANGES`) and had already
 * drifted on labels ('Last 7 days' vs '7d') — this is the one source of
 * value → days → label truth so they can't drift again (#349).
 *
 * Each surface keeps its own filtering *mechanism* (client-side filter vs
 * server `days` param); only the preset definitions are unified here.
 */
export const WINDOW_PRESETS = [
  { value: '1', days: 1, label: 'Last 24h' },
  { value: '7', days: 7, label: 'Last 7 days' },
  { value: '30', days: 30, label: 'Last 30 days' },
] as const;

export type WindowPresetValue = (typeof WINDOW_PRESETS)[number]['value'];
