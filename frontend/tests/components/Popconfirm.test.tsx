import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, ConfigProvider } from 'antd';
import { describe, expect, it, vi } from 'vitest';

import { Popconfirm } from '../../src/components/shared/Popconfirm';

// Keyboard operation of every confirmation popover (#1672), on antd's real Popconfirm. Motion is
// off: jsdom never ends an animation.
function renderConfirm(danger: boolean) {
  const onConfirm = vi.fn();
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <Popconfirm
        title="Remove this member?"
        okText="Remove"
        okButtonProps={danger ? { danger: true } : undefined}
        onConfirm={onConfirm}
      >
        <Button>Remove member</Button>
      </Popconfirm>
    </ConfigProvider>,
  );
  return onConfirm;
}

describe('shared Popconfirm (#1672)', () => {
  it('opens on Cancel when confirming destroys something, so Enter does not delete', async () => {
    const user = userEvent.setup();
    const onConfirm = renderConfirm(true);
    screen.getByRole('button', { name: 'Remove member' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());
    await user.keyboard('{Enter}');
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('opens on OK when confirming destroys nothing', async () => {
    const user = userEvent.setup();
    renderConfirm(false);
    screen.getByRole('button', { name: 'Remove member' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove' })).toHaveFocus());
  });

  it('returns focus to its trigger when it closes', async () => {
    const user = userEvent.setup();
    renderConfirm(true);
    const trigger = screen.getByRole('button', { name: 'Remove member' });
    trigger.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());
    await user.keyboard('{Enter}');
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
