import { useEffect, useMemo, useState, type ReactNode } from 'react';

import {
  ThemeModeContext,
  type ThemeModePreference,
  type ResolvedThemeMode,
} from './themeModeContext';

const STORAGE_KEY = 'dq-theme-mode';

function readStoredMode(): ThemeModePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // localStorage unavailable (private browsing, disabled) — fall back below.
  }
  return 'system';
}

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeModePreference>(readStoredMode);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  const resolvedMode: ResolvedThemeMode =
    mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolvedMode);
  }, [resolvedMode]);

  function setMode(next: ThemeModePreference) {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort persistence — the in-memory mode still applies this session.
    }
  }

  const value = useMemo(() => ({ mode, resolvedMode, setMode }), [mode, resolvedMode]);

  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>;
}
