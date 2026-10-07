import { act, render, screen } from '@testing-library/react';
import { App } from 'antd';
import { describe, expect, it } from 'vitest';

import { DemoBadge } from '../../src/demo/DemoBadge';
import { INSTALL_GUIDE_URL } from '../../src/demo/flag';
import { announce } from '../../src/demo/notices';

const mount = (compact = false) =>
  render(
    <App>
      <DemoBadge compact={compact} />
    </App>,
  );

describe('DemoBadge', () => {
  it('labels the shell as a demo and links to the install guide', () => {
    mount();
    expect(screen.getByText('Read-only demo · sample data')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Run DataQ yourself in 5 minutes' })).toHaveAttribute(
      'href',
      INSTALL_GUIDE_URL,
    );
  });

  it('shrinks to a tag on a narrow header', () => {
    mount(true);
    expect(screen.getByText('Demo')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('explains a refused write once, however many requests were refused', async () => {
    mount();
    act(() => {
      announce('read-only');
      announce('read-only');
    });
    expect(await screen.findAllByText('Read-only demo')).toHaveLength(1);
    expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
  });

  it.each([
    ['not-recorded', 'Not in the demo'],
    ['approximate', 'Showing the default view'],
  ] as const)('explains %s', async (kind, title) => {
    mount();
    act(() => announce(kind));
    expect(await screen.findByText(title)).toBeInTheDocument();
  });

  it('stops listening once unmounted', () => {
    const { unmount } = mount();
    unmount();
    act(() => announce('read-only'));
    expect(screen.queryByText('Read-only demo')).not.toBeInTheDocument();
  });
});
