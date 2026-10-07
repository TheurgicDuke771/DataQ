/** What the demo could not do for the visitor — raised by the adapter, shown by `DemoBadge`. */
export type DemoNotice = 'read-only' | 'not-recorded' | 'approximate';

export const DEMO_NOTICE_EVENT = 'dataq-demo-notice';

export function announce(kind: DemoNotice): void {
  window.dispatchEvent(new CustomEvent<DemoNotice>(DEMO_NOTICE_EVENT, { detail: kind }));
}
