import { createContext } from 'react';

export type ThemeModePreference = 'light' | 'dark' | 'system';
export type ResolvedThemeMode = 'light' | 'dark';

export interface ThemeModeContextValue {
  mode: ThemeModePreference;
  resolvedMode: ResolvedThemeMode;
  setMode: (mode: ThemeModePreference) => void;
}

export const ThemeModeContext = createContext<ThemeModeContextValue | null>(null);
