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
let now = 0;

beforeEach(() => {
  maxScroll = 10_000;
  frames = [];
  now = 0;
  sessionStorage.clear();
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get(this: Element) {
      return scrolls.get(this) ?? 0;
    },
    set(this: Element, v: number) {
      scrolls.set(this, Math.max(0, Math.min(v, maxScroll)));
    },
  });
  const ids = new Map<number, FrameRequestCallback>();
  let next = 0;
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    ids.set(++next, cb);
    frames.push(cb);
    return next;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
    const cb = ids.get(id);
    frames = frames.filter((f) => f !== cb);
  });
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

function runFramesOnce() {
  const batch = frames;
  frames = [];
  act(() => batch.forEach((cb) => cb(0)));
}

function Nav() {
  const navigate = useNavigate();
  return (
    <nav>
      <button onClick={() => navigate('/a')}>to a</button>
      <button onClick={() => navigate('/b')}>to b</button>
      <button onClick={() => navigate('/form')}>to form</button>
      <button onClick={() => navigate('/a?tab=2', { replace: true })}>replace query</button>
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
          {/* A page that focuses its own field on mount. */}
          <Route path="/form" element={<input aria-label="name" autoFocus />} />
        </Routes>
      </main>
    </>
  );
}

function setup() {
  return mount();
}

function mount() {
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
    scrollTo(150);
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    // /a was never scrolled, so it has no recorded offset: it is not left at /b's 150.
    expect(main.scrollTop).toBe(0);
  });

  it('opens a page that focuses its own field at the top, leaving that focus', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to form' }));
    expect(main.scrollTop).toBe(0);
    expect(screen.getByRole('textbox', { name: 'name' })).toHaveFocus();
  });

  it('keeps the offsets across a reload of the tab', () => {
    const first = setup();
    first.scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    window.dispatchEvent(new Event('pagehide'));
    // What survives a reload is sessionStorage; the entry keys come back from history.state,
    // which MemoryRouter's fixed initial key stands in for.
    expect(sessionStorage.getItem('dq-main-scroll')).toContain('400');
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
    fireEvent.wheel(main);
    scrollTo(20);
    maxScroll = 10_000;
    runFrames();
    expect(main.scrollTop).toBe(20);
  });

  it('does not take a clamped position for the user scrolling, nor record it', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    maxScroll = 100;
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    // The page shrinks further while loading: the browser clamps and fires scroll.
    maxScroll = 50;
    main.scrollTop = 400;
    fireEvent.scroll(main);
    runFramesOnce();
    maxScroll = 10_000;
    runFrames();
    expect(main.scrollTop).toBe(400);
    // …and the clamped 50 was not recorded over the entry's 400: a later Back still finds it.
    fireEvent.click(screen.getByRole('button', { name: 'forward' }));
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(main.scrollTop).toBe(400);
  });

  it('keeps restoring through a query-string replace on the page being restored', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    maxScroll = 100;
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    fireEvent.click(screen.getByRole('button', { name: 'replace query' }));
    maxScroll = 10_000;
    runFrames();
    expect(main.scrollTop).toBe(400);
  });

  it('gives up restoring a page that never gets tall enough', () => {
    const { main, scrollTo } = setup();
    scrollTo(400);
    fireEvent.click(screen.getByRole('button', { name: 'to b' }));
    maxScroll = 100;
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    now = 3001;
    runFramesOnce();
    expect(frames).toHaveLength(0);
    expect(main.scrollTop).toBe(100);
  });
});
