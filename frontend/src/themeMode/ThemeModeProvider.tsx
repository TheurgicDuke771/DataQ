import { useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';
import { flushSync } from 'react-dom';

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
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    // matchMedia unsupported/unavailable — default to light.
    return false;
  }
}

export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeModePreference>(readStoredMode);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia('(prefers-color-scheme: dark)');
    } catch {
      return;
    }
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  // Paper is white whatever the screen theme: browsers drop page backgrounds when printing, so
  // the dark theme's light ink (and antd's algorithm-painted surfaces, which no stylesheet can
  // reach) would print faint or invisible. The whole app renders light for the print, and
  // synchronously: window.print() and Ctrl+P snapshot the page right after `beforeprint`.
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener('beforeprint', before);
    window.addEventListener('afterprint', after);
    return () => {
      window.removeEventListener('beforeprint', before);
      window.removeEventListener('afterprint', after);
    };
  }, []);

  const resolvedMode: ResolvedThemeMode = printing
    ? 'light'
    : mode === 'system'
      ? systemDark
        ? 'dark'
        : 'light'
      : mode;

  // A layout effect, so the attribute changes in the same synchronous commit as the print render.
  useLayoutEffect(() => {
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
