import { Modal as AntModal, type ModalProps } from 'antd';
import { useEffect, useId, useMemo } from 'react';

/** The transition every dialog opens with (#2466). antd's default zooms the dialog in from
 *  a small scale, and a Select inside it sizes its dropdown from the trigger's on-screen
 *  width when it opens: clicked during the zoom, the dropdown stays a sliver of the field.
 *  A fade never scales, so the measurement is always the real width. */
export const MODAL_TRANSITION = 'ant-fade';

/** A field a keyboard user would type into, in the dialog's body. */
const FIELD = [
  'input:not([type="hidden"]):not([type="file"]):not([disabled]):not([readonly])',
  'textarea:not([disabled]):not([readonly])',
  '[role="combobox"]:not([aria-disabled="true"])',
  '[contenteditable="true"]',
].join(', ');
/** How long to wait for fields that render after the dialog opens (an edit form behind a fetch). */
const LATE_FIELD_MS = 2000;
const WRAP_ID_PREFIX = 'dq-modal-';

/** Each open dialog's opener, by its wrapper id: a dialog opened from inside another that is
 *  closing in the same render inherits that one's opener, since its own is about to go. */
const openers = new Map<string, Element | null>();

/** antd's Modal, opening with a fade instead of a zoom. Use it for every dialog; ESLint
 *  rejects importing `Modal` from antd directly. For `confirm()`-style dialogs, use
 *  `useAppModal`.
 *
 *  Two keyboard fixes every dialog gets (#1672):
 *  - **Focus starts in the first visible field,** not on the dialog frame or the × Close button
 *    antd focuses by default, including a field that renders after a load. antd moves focus to
 *    its frame as it opens, over a React `autoFocus`, so a dialog that should start elsewhere
 *    marks that control `data-dq-autofocus`.
 *  - **Focus returns to the opener when the dialog unmounts.** antd returns it when `open`
 *    turns false, but a dialog rendered as `{x && <Modal open />}` unmounts instead, and focus
 *    would be left on `<body>`. A caller's `focusable={{ focusTriggerAfterClose: false }}`
 *    turns this off too. */
export function Modal({ afterOpenChange, wrapProps, ...props }: ModalProps) {
  const id = useId();
  const wrapId = (wrapProps as { id?: string } | undefined)?.id ?? `${WRAP_ID_PREFIX}${id}`;
  const returnFocus = props.focusable?.focusTriggerAfterClose !== false;
  // The element that had focus when the dialog opened, read in the render that opens it, before
  // the dialog takes focus.
  const opener = useMemo(
    () => (props.open ? openerOf(document.activeElement) : null),
    [props.open],
  );

  useEffect(() => {
    openers.set(wrapId, opener);
    return () => {
      openers.delete(wrapId);
      if (!returnFocus) return;
      // After the dialog's DOM is gone, and after antd's own return, if any.
      requestAnimationFrame(() => {
        const active = document.activeElement;
        const lost = !active || active === document.body || !active.isConnected;
        // Another dialog open now owns focus; it returns it when it closes.
        const otherDialog = document.querySelector('[role="dialog"][aria-modal="true"]');
        if (lost && !otherDialog && opener instanceof HTMLElement && opener.isConnected) {
          opener.focus();
        }
      });
    };
  }, [wrapId, opener, returnFocus]);

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

/** ``active`` — or, when it sits in another of these dialogs, that dialog's own opener. */
function openerOf(active: Element | null): Element | null {
  const wrap = active?.closest(`[id^="${WRAP_ID_PREFIX}"]`);
  return wrap && openers.has(wrap.id) ? (openers.get(wrap.id) ?? null) : active;
}

function visible(el: HTMLElement): boolean {
  if (el.closest('[hidden], [aria-hidden="true"], .ant-form-item-hidden')) return false;
  return typeof el.checkVisibility === 'function' ? el.checkVisibility() : true;
}

/** Whether focus is still where antd put it: on the dialog element, or on its Close button. */
function onDefault(wrap: HTMLElement): boolean {
  const active = document.activeElement;
  if (!active || active === document.body || !wrap.contains(active)) return true;
  return active.getAttribute('role') === 'dialog' || active.getAttribute('aria-label') === 'Close';
}

function firstField(wrap: HTMLElement): HTMLElement | null {
  const marked = wrap.querySelector<HTMLElement>('[data-dq-autofocus]');
  if (marked) return marked;
  const body = wrap.querySelector('[class$="-modal-body"], [class*="-modal-body "]') ?? wrap;
  return [...body.querySelectorAll<HTMLElement>(FIELD)].find(visible) ?? null;
}

function focusFirstField(wrapId: string): void {
  const wrap = document.getElementById(wrapId);
  if (!wrap || !onDefault(wrap)) return;
  const field = firstField(wrap);
  if (field) {
    field.focus();
    return;
  }
  // Nothing to type into yet: the form may render once its data loads.
  const observer = new MutationObserver(() => {
    if (!wrap.isConnected || !onDefault(wrap)) return stop();
    const late = firstField(wrap);
    if (late) {
      late.focus();
      stop();
    }
  });
  const timer = window.setTimeout(() => stop(), LATE_FIELD_MS);
  function stop() {
    observer.disconnect();
    window.clearTimeout(timer);
  }
  observer.observe(wrap, { childList: true, subtree: true });
}
