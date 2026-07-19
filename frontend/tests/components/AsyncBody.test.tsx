import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AsyncBody } from '../../src/components/AsyncBody';
import type { AsyncState } from '../../src/hooks/useAsyncData';

/**
 * The shared loading/error/ok ladder (#229). The `'ok'` branch is type-narrowed
 * through the render-prop, so the child receives the data, never the state.
 */
describe('AsyncBody', () => {
  const renderChild = (data: string[]) => <div>rows: {data.length}</div>;

  it('renders the loading caption while loading', () => {
    const state: AsyncState<string[]> = { status: 'loading' };
    render(
      <AsyncBody state={state} loadingText="Loading things…" errorTitle="Failed">
        {renderChild}
      </AsyncBody>,
    );
    expect(screen.getByText('Loading things…')).toBeInTheDocument();
  });

  it('renders the error title and message on failure', () => {
    const state: AsyncState<string[]> = { status: 'error', error: 'boom', kind: 'http' as const };
    render(
      <AsyncBody state={state} errorTitle="Failed to load">
        {renderChild}
      </AsyncBody>,
    );
    expect(screen.getByText('Failed to load')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('renders the data via the render-prop on ok', () => {
    const state: AsyncState<string[]> = { status: 'ok', data: ['a', 'b'] };
    render(
      <AsyncBody state={state} errorTitle="Failed">
        {renderChild}
      </AsyncBody>,
    );
    expect(screen.getByText('rows: 2')).toBeInTheDocument();
  });
});
