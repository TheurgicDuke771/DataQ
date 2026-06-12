import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { downloadJson, toFilenameStem } from '../../src/utils/download';

describe('toFilenameStem', () => {
  it('lowercases and collapses non-word runs to single underscores', () => {
    expect(toFilenameStem('Orders Suite')).toBe('orders_suite');
    expect(toFilenameStem('  A / B  ')).toBe('a_b');
    expect(toFilenameStem('orders__suite')).toBe('orders_suite');
  });

  it('falls back when nothing usable remains', () => {
    expect(toFilenameStem('!!!')).toBe('suite');
    expect(toFilenameStem('###', 'doc')).toBe('doc');
  });
});

describe('downloadJson', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:mock');
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => vi.restoreAllMocks());

  it('serialises pretty JSON, clicks an anchor, and revokes the URL', async () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    downloadJson('suite.json', { a: 1 });

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    expect(blob.type).toBe('application/json');
    expect(await blob.text()).toBe('{\n  "a": 1\n}');
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock');
  });
});
