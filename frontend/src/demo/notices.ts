/** What the demo could not do for the visitor — raised by the adapter, shown by `DemoBadge`. */
export type DemoNotice = 'read-only' | 'not-recorded' | 'approximate';

/** Also dispatched on `window`, so a browser test can observe notices without the UI. */
export const DEMO_NOTICE_EVENT = 'dataq-demo-notice';

type Listener = (kind: DemoNotice) => void;
const listeners = new Set<Listener>();
// The badge is a lazy chunk: a request can be refused before it has mounted.
const unseen = new Set<DemoNotice>();

export function announce(kind: DemoNotice): void {
  window.dispatchEvent(new CustomEvent<DemoNotice>(DEMO_NOTICE_EVENT, { detail: kind }));
  if (listeners.size === 0) unseen.add(kind);
  else listeners.forEach((listener) => listener(kind));
}

/** Receive notices, starting with any raised before there was a listener. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  unseen.forEach((kind) => listener(kind));
  unseen.clear();
  return () => {
    listeners.delete(listener);
  };
}
