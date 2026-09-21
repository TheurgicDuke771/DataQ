import { useEffect } from 'react';

const SELECTOR = '.ant-table-content, .ant-table-body';
const MARK = 'data-dq-scroll-focus';

function syncOne(el: HTMLElement): void {
  const overflows = el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight;
  if (overflows && !el.hasAttribute(MARK)) {
    el.setAttribute(MARK, '');
    el.tabIndex = 0;
    // `group`, not `region`: a landmark per table floods landmark navigation, and run detail
    // nests one overflowing table inside another.
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', 'Scrollable table');
  } else if (!overflows && el.hasAttribute(MARK)) {
    el.removeAttribute(MARK);
    el.removeAttribute('tabindex');
    el.removeAttribute('role');
    el.removeAttribute('aria-label');
  }
}

/** Makes every antd table scroll container keyboard-reachable while it actually overflows —
 *  a scrolling region nothing can focus cannot be scrolled without a mouse. Mounted once in the
 *  shell because rc-table owns that element and exposes no prop for it. */
export function ScrollableTableFocus(): null {
  useEffect(() => {
    let frame = 0;
    const watched = new WeakSet<Element>();
    // Size changes reach overflow without any node being added: a cell's text growing on a
    // poll, a sider collapse. Watching the container and its table covers both directions.
    const resizes =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => schedule());
    const sync = () => {
      for (const el of document.querySelectorAll<HTMLElement>(SELECTOR)) {
        if (resizes && !watched.has(el)) {
          watched.add(el);
          resizes.observe(el);
          if (el.firstElementChild) resizes.observe(el.firstElementChild);
        }
        syncOne(el);
      }
    };
    function schedule() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(sync);
    }
    const mutations = new MutationObserver(schedule);
    mutations.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      mutations.disconnect();
      resizes?.disconnect();
      window.removeEventListener('resize', schedule);
    };
  }, []);
  return null;
}
