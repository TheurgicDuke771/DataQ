import { useEffect } from 'react';

const SELECTOR = '.ant-table-content, .ant-table-body';
const MARK = 'data-dq-scroll-focus';

function sync(): void {
  for (const el of document.querySelectorAll<HTMLElement>(SELECTOR)) {
    const overflows = el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight;
    if (overflows && !el.hasAttribute(MARK)) {
      el.setAttribute(MARK, '');
      el.tabIndex = 0;
      el.setAttribute('role', 'region');
      el.setAttribute('aria-label', 'Scrollable table');
    } else if (!overflows && el.hasAttribute(MARK)) {
      el.removeAttribute(MARK);
      el.removeAttribute('tabindex');
      el.removeAttribute('role');
      el.removeAttribute('aria-label');
    }
  }
}

/** Makes every antd table scroll container keyboard-reachable while it actually overflows —
 *  a scrolling region nothing can focus cannot be scrolled without a mouse. Mounted once in the
 *  shell because rc-table owns that element and exposes no prop for it. */
export function ScrollableTableFocus(): null {
  useEffect(() => {
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(sync);
    };
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', schedule);
    };
  }, []);
  return null;
}
