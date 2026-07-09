import { screen } from '@testing-library/react';
import type userEvent from '@testing-library/user-event';

type User = ReturnType<typeof userEvent.setup>;

/**
 * Open the antd Select at combobox `index` (default 0) and pick an option.
 *
 * antd renders a truncated `role=option` a11y mirror, but the real dropdown
 * items carry the label both as a `title` attribute and inside
 * `.ant-select-item-option-content`. Match by `title` (default) or by the
 * option-content `text` — the two idioms that were copy-pasted across the
 * component tests (#197). Coupling to the antd internal class lives here only,
 * so a future antd bump is a one-line change.
 */
export async function selectOption(
  user: User,
  option: string,
  { index = 0, by = 'title' }: { index?: number; by?: 'title' | 'text' } = {},
): Promise<void> {
  await user.click((await screen.findAllByRole('combobox'))[index]);
  const item =
    by === 'text'
      ? await screen.findByText(option, { selector: '.ant-select-item-option-content' })
      : await screen.findByTitle(option);
  await user.click(item);
}
