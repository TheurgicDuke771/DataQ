import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { type ReactNode, useState } from 'react';
import { describe, expect, it } from 'vitest';

import { SnoozedTag } from '../../src/components/checks/snooze';
import { Modal } from '../../src/components/shared/Modal';
import type { Check } from '../../src/api/suites';

// Keyboard behaviour of every dialog (#1672), on antd's real Modal. Motion is off: jsdom never
// ends an animation, so antd would never report the dialog open.
const still = (ui: ReactNode) => (
  <ConfigProvider theme={{ token: { motion: false } }}>{ui}</ConfigProvider>
);

function Mounted({ autoFocusOk = false }: { autoFocusOk?: boolean }) {
  // Rendered only while open: the pattern that left focus on <body> when it closed.
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>New entry</button>
      {open && (
        <Modal open title="New entry" onCancel={() => setOpen(false)} footer={null}>
          <label>
            Name <input aria-label="Name" />
          </label>
          {autoFocusOk && <button data-dq-autofocus>Chosen</button>}
        </Modal>
      )}
    </>
  );
}

describe('shared Modal focus (#1672)', () => {
  it('starts in the first field rather than on Close', async () => {
    const user = userEvent.setup();
    render(still(<Mounted />));
    await user.click(screen.getByRole('button', { name: 'New entry' }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus());
  });

  it('returns focus to the opener when a dialog rendered only while open goes away', async () => {
    const user = userEvent.setup();
    render(still(<Mounted />));
    const opener = screen.getByRole('button', { name: 'New entry' });
    await user.click(opener);
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus());
    await user.keyboard('{Escape}');
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it('starts on the control a dialog marks, when it marks one', async () => {
    const user = userEvent.setup();
    render(still(<Mounted autoFocusOk />));
    await user.click(screen.getByRole('button', { name: 'New entry' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Chosen' })).toHaveFocus());
  });
});

describe('SnoozedTag (#1672)', () => {
  it('can be reached by keyboard, so its explanation is not hover-only', async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    render(<SnoozedTag check={{ alert_snoozed_until: future } as Check} />);
    const tag = screen.getByText(/Snoozed until/);
    expect(tag).toHaveAttribute('tabindex', '0');
    await act(async () => tag.focus());
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/won't trigger alerts/);
  });
});
