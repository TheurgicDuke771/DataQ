import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { BulkThresholdsModal } from '../../src/components/checks/BulkThresholdsModal';

function renderModal(onApply = vi.fn().mockResolvedValue(undefined)) {
  render(<BulkThresholdsModal open count={3} onCancel={vi.fn()} onApply={onApply} />);
  return onApply;
}

async function setMode(user: ReturnType<typeof userEvent.setup>, tier: string, option: string) {
  await user.click(screen.getByRole('combobox', { name: tier }));
  const options = await screen.findAllByText(option);
  await user.click(options[options.length - 1]);
}

describe('BulkThresholdsModal', () => {
  it('cannot be applied until a tier is changed', () => {
    renderModal();
    expect(screen.getByRole('button', { name: 'Apply to 3 checks' })).toBeDisabled();
  });

  it('sends only the tiers that were changed: a number to set, null to clear', async () => {
    const user = userEvent.setup();
    const onApply = renderModal();

    await setMode(user, 'Fail', 'Set to');
    await user.type(screen.getByRole('spinbutton', { name: 'Fail threshold' }), '10');
    await setMode(user, 'Critical', 'Clear');
    await user.click(screen.getByRole('button', { name: 'Apply to 3 checks' }));

    // Warn was left as it is, so its key is absent — each check keeps its own warn.
    await waitFor(() =>
      expect(onApply).toHaveBeenCalledWith({ fail_threshold: 10, critical_threshold: null }),
    );
  });

  it('cannot be applied while a tier is set to an empty value', async () => {
    const user = userEvent.setup();
    renderModal();

    await setMode(user, 'Warn', 'Set to');

    expect(screen.getByRole('button', { name: 'Apply to 3 checks' })).toBeDisabled();
  });

  it('says the change is all-or-nothing', () => {
    renderModal();
    expect(
      screen.getByText(/none is changed and the checks at fault are named/),
    ).toBeInTheDocument();
  });
});
