import { App as AntApp } from 'antd';
import { render, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { listConnections } from '../../src/api/connections';
import { getSuite } from '../../src/api/suites';
import { reportEvent } from '../../src/api/telemetry';
import { CheckNew } from '../../src/pages/CheckNew';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn(), listConnections: vi.fn() };
});
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createCheck: vi.fn(), getSuite: vi.fn() };
});
vi.mock('../../src/api/telemetry', () => ({ reportEvent: vi.fn() }));

vi.mocked(listConnections).mockResolvedValue([]);
vi.mocked(getSuite).mockReturnValue(new Promise(() => undefined));
const mockReport = vi.mocked(reportEvent);

function page() {
  return (
    <StrictMode>
      <MemoryRouter initialEntries={['/suites/s1/checks/new']}>
        <AntApp>
          <Routes>
            <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
          </Routes>
        </AntApp>
      </MemoryRouter>
    </StrictMode>
  );
}

afterEach(() => vi.clearAllMocks());

describe('CheckNew abandonment', () => {
  it('reports nothing while the editor is open, even under StrictMode', async () => {
    render(page());
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockReport).not.toHaveBeenCalled();
  });

  it('reports the step when the editor is left without saving', async () => {
    const { unmount } = render(page());
    // A previous test's page is unmounted by the automatic cleanup; let its report land first.
    await new Promise((resolve) => setTimeout(resolve, 10));
    mockReport.mockClear();
    unmount();
    await waitFor(() =>
      expect(mockReport).toHaveBeenCalledWith({
        name: 'check_editor_abandoned',
        attributes: { step: 'expectation' },
      }),
    );
    expect(mockReport).toHaveBeenCalledTimes(1);
  });
});
