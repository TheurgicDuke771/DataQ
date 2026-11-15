import { Popconfirm as AntPopconfirm, type PopconfirmProps } from 'antd';
import { useEffect, useId, useRef } from 'react';

/** Frames to wait for the popup to render after it opens. */
const OPEN_FRAMES = 10;
/** A field the popup asks for (the Resolve confirm's "What was it?"), focused before its buttons. */
const FIELD = 'input:not([disabled]), textarea:not([disabled]), [role="combobox"]';

/** antd's Popconfirm, operable by keyboard (#1672). antd leaves focus on the trigger when it
 *  opens, so a keyboard user had to Tab to find its buttons, and when it closed focus could be
 *  left on `<body>`. This one:
 *  - **starts in the popup:** on a field it asks for, if any; else on Cancel when confirming
 *    would destroy something (a danger OK), so Enter pressed as it opens never deletes; else on
 *    OK. A danger confirm with no Cancel leaves focus where it is rather than on its OK;
 *  - **returns focus** when it closes from inside (Escape, OK, Cancel): to its trigger, or to the
 *    page's `<main>` when the action removed the trigger (a deleted row). A close by clicking
 *    elsewhere leaves focus where that click put it.
 *  Works for a controlled `open` too. Use it for every confirmation popover; ESLint rejects
 *  importing antd's directly. */
export function Popconfirm({
  okButtonProps,
  okType,
  onOpenChange,
  rootClassName,
  open,
  ...props
}: PopconfirmProps) {
  const marker = `dq-popconfirm-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const opener = useRef<Element | null>(null);
  const isOpen = useRef(false);
  const danger = okType === 'danger' || okButtonProps?.danger === true;

  const focusInside = (frames = OPEN_FRAMES) => {
    const popup = document.querySelector(`.${marker}`);
    const field = popup?.querySelector<HTMLElement>(`.ant-popconfirm-message ${FIELD}`) ?? null;
    const buttons = popup
      ? [...popup.querySelectorAll<HTMLElement>('.ant-popconfirm-buttons button')]
      : [];
    // Cancel is the first of two buttons, OK the last; a lone button is OK.
    const target =
      field ?? (danger ? (buttons.length > 1 ? buttons[0] : null) : (buttons.at(-1) ?? null));
    if (target) target.focus({ preventScroll: true });
    else if (frames > 0 && buttons.length === 0)
      requestAnimationFrame(() => focusInside(frames - 1));
  };

  const changed = (next: boolean) => {
    if (next === isOpen.current) return;
    isOpen.current = next;
    if (next) {
      opener.current = document.activeElement;
      requestAnimationFrame(() => focusInside());
      return;
    }
    // Only a close from inside the popup moves focus; a click elsewhere chose its own.
    if (!document.activeElement?.closest(`.${marker}`)) return;
    const trigger = opener.current;
    requestAnimationFrame(() => {
      const active = document.activeElement;
      const lost =
        !active || active === document.body || !active.isConnected || active.closest(`.${marker}`);
      if (!lost) return;
      const target =
        trigger instanceof HTMLElement && trigger.isConnected
          ? trigger
          : document.getElementById('dq-main');
      target?.focus({ preventScroll: true });
    });
  };

  // A controlled popover opens and closes through `open`, not through onOpenChange.
  useEffect(() => {
    if (open !== undefined) changed(open);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- react to `open` alone
  }, [open]);

  return (
    <AntPopconfirm
      {...props}
      open={open}
      okType={okType}
      okButtonProps={okButtonProps}
      rootClassName={[rootClassName, marker].filter(Boolean).join(' ')}
      onOpenChange={(next: boolean) => {
        changed(next);
        onOpenChange?.(next);
      }}
    />
  );
}
