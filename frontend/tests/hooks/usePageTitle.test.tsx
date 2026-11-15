import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { usePageTitle } from '../../src/hooks/usePageTitle';
import { namedPageTitle } from '../../src/utils/pageTitle';

describe('usePageTitle', () => {
  afterEach(() => {
    document.title = '';
  });

  it('titles the document once the record loads and restores the path title on unmount', () => {
    document.title = 'Suite · DataQ';
    const { rerender, unmount } = renderHook(({ title }) => usePageTitle(title), {
      initialProps: { title: null as string | null },
    });
    expect(document.title).toBe('Suite · DataQ'); // still loading: the path title stays

    rerender({ title: namedPageTitle('orders', 'Suite') });
    expect(document.title).toBe('orders · Suite · DataQ');

    unmount();
    expect(document.title).toBe('Suite · DataQ');
  });

  it('follows a change of record on the same page', () => {
    document.title = 'Suite · DataQ';
    const { rerender } = renderHook(({ title }) => usePageTitle(title), {
      initialProps: { title: namedPageTitle('orders', 'Suite') },
    });
    rerender({ title: namedPageTitle('refunds', 'Suite') });
    expect(document.title).toBe('refunds · Suite · DataQ');
  });
});
