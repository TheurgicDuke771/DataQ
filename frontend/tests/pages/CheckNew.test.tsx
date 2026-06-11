import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Check, createCheck } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createCheck: vi.fn() };
});

const mockCreate = vi.mocked(createCheck);

// Render the page at /suites/s1/checks/new with a stub suite-detail route so the
// post-create navigation has somewhere to land.
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/s1/checks/new']}>
      <AntApp>
        <Routes>
          <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
          <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CheckNew', () => {
  it('shows the real categories plus reserved (disabled) ones', () => {
    renderPage();
    expect(screen.getByText('Column values')).toBeInTheDocument();
    expect(screen.getByText('Table shape')).toBeInTheDocument();
    // Reserved monitor-kind categories surface as roadmap markers.
    expect(screen.getByText('Freshness')).toBeInTheDocument();
    expect(screen.getByText('Schema drift')).toBeInTheDocument();
  });

  it('walks category → expectation → config, creates, and navigates back', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    // Step 1 → pick a category.
    await user.click(screen.getByText('Column values'));
    // Step 2 → pick an expectation within it.
    await user.click(await screen.findByText('Column values in set'));
    // Step 3 → fill config (value-set list is split/trimmed) + name.
    await user.type(await screen.findByLabelText('Name'), 'status in set');
    await user.type(screen.getByLabelText('Column'), 'status');
    await user.type(screen.getByLabelText('Allowed values'), 'active, closed , pending');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'status in set',
      expectation_type: 'expect_column_values_to_be_in_set',
      config: { column: 'status', value_set: ['active', 'closed', 'pending'] },
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
    });
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
  });

  it('rejects a delimiter-only value-set instead of saving an empty config', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByText('Column values'));
    await user.click(await screen.findByText('Column values in set'));
    await user.type(await screen.findByLabelText('Name'), 'bad set');
    await user.type(screen.getByLabelText('Column'), 'status');
    await user.type(screen.getByLabelText('Allowed values'), ' , ');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(await screen.findByText('Enter at least one value')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
