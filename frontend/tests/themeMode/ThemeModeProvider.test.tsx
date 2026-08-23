import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ThemeModeProvider } from '../../src/themeMode/ThemeModeProvider';
import { useThemeMode } from '../../src/themeMode/useThemeMode';

const STORAGE_KEY = 'dq-theme-mode';

/** A stub matchMedia whose `matches` and change listener the test controls directly. */
function mockMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const mql = {
    get matches() {
      return matches;
    },
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, listener: (e: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_: string, listener: (e: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    },
  } as unknown as MediaQueryList;
  window.matchMedia = vi.fn().mockReturnValue(mql);
  return {
    setMatches(next: boolean) {
      matches = next;
      listeners.forEach((l) => l({ matches: next } as MediaQueryListEvent));
    },
  };
}

function Probe() {
  const { mode, resolvedMode, setMode } = useThemeMode();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="resolved">{resolvedMode}</span>
      <button onClick={() => setMode('dark')}>dark</button>
      <button onClick={() => setMode('light')}>light</button>
      <button onClick={() => setMode('system')}>system</button>
    </div>
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('ThemeModeProvider', () => {
  it('defaults to system, resolved from the OS preference', () => {
    mockMatchMedia(true);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    expect(screen.getByTestId('mode')).toHaveTextContent('system');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('reads a persisted explicit preference over the OS default', () => {
    localStorage.setItem(STORAGE_KEY, 'light');
    mockMatchMedia(true);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    expect(screen.getByTestId('mode')).toHaveTextContent('light');
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
  });

  it('ignores a corrupt stored value and falls back to system', () => {
    localStorage.setItem(STORAGE_KEY, 'sepia');
    mockMatchMedia(false);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    expect(screen.getByTestId('mode')).toHaveTextContent('system');
  });

  it('setMode persists the choice and updates data-theme', () => {
    mockMatchMedia(false);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'dark' }));
    expect(screen.getByTestId('mode')).toHaveTextContent('dark');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
  });

  it('tracks a live OS preference change while mode is system', () => {
    const media = mockMatchMedia(false);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
    act(() => media.setMatches(true));
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });

  it('stops tracking the OS preference once an explicit mode is set', () => {
    const media = mockMatchMedia(false);
    render(
      <ThemeModeProvider>
        <Probe />
      </ThemeModeProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'light' }));
    act(() => media.setMatches(true));
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
  });

  it('useThemeMode throws outside a ThemeModeProvider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow('useThemeMode must be used within a ThemeModeProvider');
    spy.mockRestore();
  });
});
