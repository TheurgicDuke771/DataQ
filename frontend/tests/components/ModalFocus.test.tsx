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

function Late() {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Edit</button>
      <button onClick={() => setLoaded(true)}>Load</button>
      {open && (
        <Modal open title="Edit" footer={null}>
          {loaded ? <input aria-label="Name" /> : <p>Loading…</p>}
        </Modal>
      )}
    </>
  );
}

function OptOut() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Search</button>
      {open && (
        <Modal
          open
          title="Search"
          footer={null}
          onCancel={() => setOpen(false)}
          focusable={{ focusTriggerAfterClose: false }}
        >
          <input aria-label="Query" />
        </Modal>
      )}
    </>
  );
}

function Wizard() {
  const [step, setStep] = useState(0);
  return (
    <>
      <button onClick={() => setStep(1)}>Start</button>
      {step === 1 && (
        <Modal open title="Step one" footer={null} onCancel={() => setStep(0)}>
          <button onClick={() => setStep(2)}>Next</button>
        </Modal>
      )}
      {step === 2 && (
        <Modal open title="Step two" footer={null} onCancel={() => setStep(0)}>
          <input aria-label="Answer" />
        </Modal>
      )}
    </>
  );
}

describe('shared Modal focus, edge cases (#1672)', () => {
  it('returns to the first opener when one dialog replaced another', async () => {
    const user = userEvent.setup();
    render(still(<Wizard />));
    const start = screen.getByRole('button', { name: 'Start' });
    await user.click(start);
    await user.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Answer' })).toHaveFocus());
    await user.keyboard('{Escape}');
    await waitFor(() => expect(start).toHaveFocus());
  });

  it('focuses a field that renders after the dialog opened', async () => {
    const user = userEvent.setup();
    render(still(<Late />));
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    act(() => screen.getByRole('button', { name: 'Load', hidden: true }).click());
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus());
  });

  it('does not return focus when the caller turned that off', async () => {
    const user = userEvent.setup();
    render(still(<OptOut />));
    const opener = screen.getByRole('button', { name: 'Search' });
    await user.click(opener);
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Query' })).toHaveFocus());
    await user.keyboard('{Escape}');
    await new Promise((r) => setTimeout(r, 50));
    expect(opener).not.toHaveFocus();
  });
});

describe('SnoozedTag (#1672)', () => {
  it('can be reached by keyboard, so its explanation is not hover-only', async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    render(<SnoozedTag check={{ alert_snoozed_until: future } as Check} />);
    const tag = screen.getByText(/Snoozed until/);
    expect(tag).toHaveAttribute('tabindex', '0');
    await act(async () => tag.focus());
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(/won't trigger alerts/);
    // Tied to the badge, so a screen reader reads it when the badge takes focus.
    expect(tag.closest('[aria-describedby]')?.getAttribute('aria-describedby')).toBe(tooltip.id);
  });
});
