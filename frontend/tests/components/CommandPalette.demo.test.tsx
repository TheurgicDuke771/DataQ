import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { search } from '../../src/api/search';
import { CommandPalette, DEMO_SEARCH_MESSAGE } from '../../src/components/shared/CommandPalette';

vi.mock('../../src/demo/flag', () => ({
  IS_DEMO: true,
  INSTALL_GUIDE_URL: 'https://example.test',
}));
vi.mock('../../src/api/search', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/search')>()),
  search: vi.fn(),
}));

describe('CommandPalette in the static demo', () => {
  it('never calls the API and says search is not part of the demo', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: /^Search/ }));
    await user.type(screen.getByRole('combobox', { name: 'Search DataQ' }), 'orders');
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(DEMO_SEARCH_MESSAGE));
    expect(vi.mocked(search)).not.toHaveBeenCalled();
  });
});
