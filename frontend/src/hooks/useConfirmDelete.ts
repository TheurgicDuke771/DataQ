import type { ReactNode } from 'react';
import { App } from 'antd';

import { errorMessage } from '../utils/errors';

export type ConfirmDeleteOptions = {
  /** Entity name — used in the default title and success message. */
  label: string;
  /** Optional explanatory body under the title. */
  content?: ReactNode;
  /** Confirm button text. Default `'Delete'`. */
  okText?: string;
  /** Toast on success. Default `` `${label} deleted` ``. */
  successMessage?: string;
  /** Prefix for the error toast. Default `'Delete failed'`. */
  errorPrefix?: string;
  /** The destructive call. */
  onDelete: () => Promise<void>;
  /** Ran after a successful delete (e.g. refetch). */
  onDone?: () => void;
};

/**
 * The danger-delete confirm modal shared by the connection / suite / check
 * delete sites: `modal.confirm({ okType: 'danger', … })` → success toast +
 * `onDone`, or an error toast plus a re-throw so the confirm modal stays open
 * on failure. The load-bearing `throw err` is exactly the bit that drifted when
 * this block was copy-pasted (#204); centralising it keeps it consistent.
 */
export function useConfirmDelete() {
  const { message, modal } = App.useApp();

  return (opts: ConfirmDeleteOptions) => {
    const { label, content, okText = 'Delete', onDelete, onDone } = opts;
    const successMessage = opts.successMessage ?? `${label} deleted`;
    const errorPrefix = opts.errorPrefix ?? 'Delete failed';

    modal.confirm({
      title: `Delete “${label}”?`,
      content,
      okText,
      okType: 'danger',
      onOk: async () => {
        try {
          await onDelete();
          message.success(successMessage);
          onDone?.();
        } catch (err) {
          message.error(`${errorPrefix}: ${errorMessage(err)}`);
          throw err; // keep the confirm modal open on failure
        }
      },
    });
  };
}
