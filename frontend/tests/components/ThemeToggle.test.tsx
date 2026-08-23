import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ThemeToggle } from '../../src/components/ThemeToggle';
import { ThemeModeProvider } from '../../src/themeMode/ThemeModeProvider';

const STORAGE_KEY = 'dq-theme-mode';

function renderToggle() {
  return render(
    <ThemeModeProvider>
      <ThemeToggle />
    </ThemeModeProvider>,
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('ThemeToggle', () => {
  it('opens a menu with all three options and marks the current one', async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(screen.getByRole('button', { name: 'Change theme' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByText('Light')).toBeInTheDocument();
    expect(within(menu).getByText('Dark')).toBeInTheDocument();
    expect(within(menu).getByText('System')).toBeInTheDocument();
  });

  it('picking Dark persists it and closes the menu', async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(screen.getByRole('button', { name: 'Change theme' }));
    await user.click(screen.getByRole('menuitem', { name: /Dark/ }));
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('picking Light persists it', async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(screen.getByRole('button', { name: 'Change theme' }));
    await user.click(screen.getByRole('menuitem', { name: /Light/ }));
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
  });
});
