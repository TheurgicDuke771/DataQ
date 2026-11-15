import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, ConfigProvider, Select } from 'antd';
import { useState } from 'react';
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

  it('starts on a field the popup asks for, before its buttons', async () => {
    const user = userEvent.setup();
    render(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <Popconfirm
          title="Resolve?"
          description={<Select aria-label="What was it?" options={[]} />}
        >
          <Button>Resolve</Button>
        </Popconfirm>
      </ConfigProvider>,
    );
    screen.getByRole('button', { name: 'Resolve' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'What was it?' })).toHaveFocus(),
    );
  });

  it('does not put a danger confirm without Cancel on its OK', async () => {
    const user = userEvent.setup();
    render(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <Popconfirm title="Purge?" showCancel={false} okButtonProps={{ danger: true }}>
          <Button>Purge</Button>
        </Popconfirm>
      </ConfigProvider>,
    );
    const trigger = screen.getByRole('button', { name: 'Purge' });
    trigger.focus();
    await user.keyboard('{Enter}');
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole('button', { name: 'OK' })).not.toHaveFocus();
  });

  it('sends focus to the page when the confirmed action removed its trigger', async () => {
    const user = userEvent.setup();
    function Row() {
      const [gone, setGone] = useState(false);
      return (
        <main id="dq-main" tabIndex={-1}>
          {!gone && (
            <Popconfirm
              title="Remove?"
              okButtonProps={{ danger: true }}
              okText="Remove"
              onConfirm={() => setGone(true)}
            >
              <Button>Remove row</Button>
            </Popconfirm>
          )}
        </main>
      );
    }
    render(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <Row />
      </ConfigProvider>,
    );
    screen.getByRole('button', { name: 'Remove row' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());
    await user.keyboard('{Tab}{Enter}');
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
  });
});
