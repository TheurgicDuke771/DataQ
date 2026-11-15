import { Popconfirm as AntPopconfirm, type PopconfirmProps } from 'antd';
import { useId, useRef } from 'react';

/** Frames to wait for the popup's buttons to render after it opens. */
const OPEN_FRAMES = 10;

/** antd's Popconfirm, operable by keyboard (#1672). antd leaves focus on the trigger when it
 *  opens, so a keyboard user had to Tab to find its buttons, and when it closed focus could be
 *  left on `<body>`. This one:
 *  - **starts on a button in the popup:** Cancel when confirming would destroy something (a
 *    danger OK), so Enter pressed as it opens never deletes; OK otherwise;
 *  - **returns focus to its trigger** when it closes with focus lost.
 *  Use it for every confirmation popover; ESLint rejects importing antd's directly. */
export function Popconfirm({
  okButtonProps,
  okType,
  onOpenChange,
  rootClassName,
  ...props
}: PopconfirmProps) {
  const marker = `dq-popconfirm-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const opener = useRef<Element | null>(null);
  const danger = okType === 'danger' || okButtonProps?.danger === true;

  const focusButton = (frames = OPEN_FRAMES) => {
    // Cancel is the first button, OK the last.
    const buttons = document.querySelectorAll<HTMLElement>(
      `.${marker} .ant-popconfirm-buttons button`,
    );
    const target = danger ? buttons[0] : buttons[buttons.length - 1];
    if (target) target.focus();
    else if (frames > 0) requestAnimationFrame(() => focusButton(frames - 1));
  };

  return (
    <AntPopconfirm
      {...props}
      okType={okType}
      okButtonProps={okButtonProps}
      rootClassName={[rootClassName, marker].filter(Boolean).join(' ')}
      onOpenChange={(open: boolean) => {
        if (open) {
          opener.current = document.activeElement;
          requestAnimationFrame(() => focusButton());
        } else {
          const trigger = opener.current;
          requestAnimationFrame(() => {
            const active = document.activeElement;
            const inPopup = active?.closest(`.${marker}`);
            const lost = !active || active === document.body || !active.isConnected || inPopup;
            if (lost && trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
          });
        }
        onOpenChange?.(open);
      }}
    />
  );
}
