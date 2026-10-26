import '@testing-library/jest-dom';

// antd's responsive components (Descriptions, Card, …) call window.matchMedia,
// which jsdom does not implement. Provide a no-op stub so they render in tests.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      // Legacy MediaQueryList API: deprecated, but antd's Carousel (via @ant-design/react-slick) still
      // calls mql.addListener/removeListener, so the stub must keep them or a responsive render throws.
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      addListener: () => {},
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

// antd's Select/virtual-list (rc-virtual-list) uses ResizeObserver, absent in
// jsdom. A no-op stub lets dropdown options render in tests.
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    // Same arity as the real constructor; the callback is kept but never invoked.
    constructor(readonly callback: ResizeObserverCallback) {}
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
