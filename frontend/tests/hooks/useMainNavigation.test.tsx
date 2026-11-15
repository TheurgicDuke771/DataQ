import { act, fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useMainNavigation } from '../../src/hooks/useMainNavigation';

// jsdom has no layout: scrollTop always reads 0. A store that clamps like a browser stands in —
// `maxScroll` is how far the current content lets <main> scroll.
let maxScroll = 10_000;
const scrolls = new WeakMap<Element, number>();
let frames: FrameRequestCallback[] = [];

beforeEach(() => {
  maxScroll = 10_000;
  frames = [];
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get(this: Element) {
      return scrolls.get(this) ?? 0;
    },
    set(this: Element, v: number) {
      scrolls.set(this, Math.max(0, Math.min(v, maxScroll)));
    },
  });
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => frames.push(cb));
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
});

afterEach(() => {
  // Back to jsdom's own (prototype-level) scrollTop.
  delete (HTMLElement.prototype as { scrollTop?: number }).scrollTop;
  vi.restoreAllMocks();
});

function runFrames() {
  for (let i = 0; i < 100 && frames.length; i++) {
    const batch = frames;
    frames = [];
    act(() => batch.forEach((cb) => cb(0)));
  }
}

function Nav() {
  const navigate = useNavigate();
  return (
    <nav>
      <button onClick={() => navigate('/a')}>to a</button>
      <button onClick={() => navigate('/b')}>to b</button>
      <button onClick={() => navigate(-1)}>back</button>
      <button onClick={() => navigate(1)}>forward</button>
    </nav>
  );
}

function InPageControl() {
  const navigate = useNavigate();
  // Stays mounted across the change, like a suite in the master list or an admin tab.
  return <button onClick={() => navigate('/b')}>in-page b</button>;
}

function Shell() {
  const ref = useRef<HTMLElement>(null);
  const onScroll = useMainNavigation(ref);
  return (
    <>
      <Nav />
      <main ref={ref} tabIndex={-1} onScroll={onScroll} data-testid="main">
        <InPageControl />
        <Routes>
          <Route path="/a" element={<p>page a</p>} />
          <Route path="/b" element={<p>page b</p>} />
        </Routes>
      </main>
    </>
  );
}

function setup() {
  render(
    <MemoryRouter initialEntries={['/a']}>
      <Shell />
    </MemoryRouter>,
  );
  const main = screen.getByTestId('main');
  const scrollTo = (y: number) => {
    main.scrollTop = y;
    fireEvent.scroll(main);
  };
  return { main, scrollTo };
}

describe('useMainNavigation', () => {
  it('opens a new page at the top, with focus on the page', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    expect(screen.getByText('page b')).toBeInTheDocument();
    expect(main.scrollTop).toBe(0);
    expect(main).toHaveFocus();
  });

  it('returns Back and Forward to the offset each entry was left at', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    scrollTo(150);
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(screen.getByText('page a')).toBeInTheDocument();
    expect(main.scrollTop).toBe(400);
    fireEvent.click(screen.getByRole('button', { name: 'forward' }));
    expect(main.scrollTop).toBe(150);
  });

  it('opens an entry it never saw at the top on Back', () => {
    const { main, scrollTo } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    // /a was never scrolled, so it has no recorded offset; it is not left at /b's.
    scrollTo(0);
    expect(main.scrollTop).toBe(0);
  });

  it('keeps focus and place when the change leaves focus on a control in the page', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    const control = screen.getByRole('button', { name: 'in-page b' });
    control.focus();
    fireEvent.click(control);
    expect(screen.getByText('page b')).toBeInTheDocument();
    expect(main.scrollTop).toBe(400);
    expect(control).toHaveFocus();
  });

  it('keeps restoring on Back while the page is still loading, until it is tall enough', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    // The returned-to page renders its rows a moment later: at first it can scroll only 100.
    maxScroll = 100;
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(main.scrollTop).toBe(100);
    maxScroll = 10_000;
    runFrames();
    expect(main.scrollTop).toBe(400);
  });

  it('stops restoring once the user scrolls', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    maxScroll = 100;
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    scrollTo(20);
    maxScroll = 10_000;
    runFrames();
    expect(main.scrollTop).toBe(20);
  });
});
