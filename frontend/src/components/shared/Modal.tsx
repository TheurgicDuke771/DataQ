import { Modal as AntModal, type ModalProps } from 'antd';
import { useEffect, useId, useMemo } from 'react';

/** The transition every dialog opens with (#2466). antd's default zooms the dialog in from
 *  a small scale, and a Select inside it sizes its dropdown from the trigger's on-screen
 *  width when it opens: clicked during the zoom, the dropdown stays a sliver of the field.
 *  A fade never scales, so the measurement is always the real width. */
export const MODAL_TRANSITION = 'ant-fade';

/** The first field a keyboard user would type into, in the dialog's body. */
const FIRST_FIELD = [
  'input:not([type="hidden"]):not([disabled])',
  'textarea:not([disabled])',
  '[role="combobox"]:not([aria-disabled="true"])',
  '[contenteditable="true"]',
].join(', ');

/** antd's Modal, opening with a fade instead of a zoom. Use it for every dialog; ESLint
 *  rejects importing `Modal` from antd directly. For `confirm()`-style dialogs, use
 *  `useAppModal`.
 *
 *  Two keyboard fixes every dialog gets (#1672):
 *  - **Focus starts in the first field,** not on the dialog frame or the × Close button antd
 *    focuses by default. antd moves focus to its frame as it opens, over a React `autoFocus`, so
 *    a dialog that should start elsewhere marks that control `data-dq-autofocus`.
 *  - **Focus returns to the opener when the dialog unmounts.** antd returns it when `open`
 *    turns false, but a dialog rendered as `{x && <Modal open />}` unmounts instead, and
 *    focus would be left on `<body>`. */
export function Modal({ afterOpenChange, wrapProps, ...props }: ModalProps) {
  const id = useId();
  const wrapId = (wrapProps as { id?: string } | undefined)?.id ?? `dq-modal-${id}`;
  // The element that had focus when the dialog opened, read in the render that opens it, before
  // the dialog takes focus.
  const opener = useMemo(() => (props.open ? document.activeElement : null), [props.open]);

  useEffect(
    () => () => {
      // After the dialog's DOM is gone, and after antd's own return, if any.
      requestAnimationFrame(() => {
        const active = document.activeElement;
        const lost = !active || active === document.body || !active.isConnected;
        if (lost && opener instanceof HTMLElement && opener.isConnected) opener.focus();
      });
    },
    [opener],
  );

  return (
    <AntModal
      {...props}
      wrapProps={{ ...(wrapProps as object | undefined), id: wrapId }}
      transitionName={props.transitionName ?? MODAL_TRANSITION}
      afterOpenChange={(open) => {
        if (open) focusFirstField(wrapId);
        afterOpenChange?.(open);
      }}
    />
  );
}

function focusFirstField(wrapId: string): void {
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  const active = document.activeElement;
  // Leave a focus the dialog chose: anything inside it other than its frame and Close button.
  const onDefault =
    !active ||
    !wrap.contains(active) ||
    active.classList.contains('ant-modal-close') ||
    active.getAttribute('role') === 'dialog' ||
    active.classList.contains('ant-modal') ||
    active.classList.contains('ant-modal-section') ||
    active.classList.contains('ant-modal-content');
  if (!onDefault) return;
  const body = wrap.querySelector('.ant-modal-body');
  (
    body?.querySelector<HTMLElement>('[data-dq-autofocus]') ??
    body?.querySelector<HTMLElement>(FIRST_FIELD)
  )?.focus();
}
