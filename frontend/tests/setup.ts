import '@testing-library/jest-dom';

// antd's responsive components (Descriptions, Card, …) call window.matchMedia,
// which jsdom does not implement. Provide a no-op stub so they render in tests.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
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
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
