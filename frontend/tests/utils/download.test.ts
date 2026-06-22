import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { downloadJson, toCsv, toFilenameStem } from '../../src/utils/download';

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

describe('toCsv', () => {
  it('joins a header row + data rows with CRLF', () => {
    expect(
      toCsv(
        ['a', 'b'],
        [
          [1, 2],
          [3, 4],
        ],
      ),
    ).toBe('a,b\r\n1,2\r\n3,4');
  });

  it('quotes fields with commas, quotes, or newlines (RFC 4180)', () => {
    const csv = toCsv(
      ['name', 'note'],
      [
        ['a,b', 'he said "hi"'],
        ['line1\nline2', 'plain'],
      ],
    );
    expect(csv).toBe('name,note\r\n"a,b","he said ""hi"""\r\n"line1\nline2",plain');
  });

  it('renders null/undefined as an empty field', () => {
    expect(toCsv(['x', 'y'], [[null, undefined]])).toBe('x,y\r\n,');
  });

  it('neutralises spreadsheet formula injection in text cells (CWE-1236)', () => {
    // Leading =, +, @ (and -, tab, CR) get an apostrophe prefix; the value is
    // then RFC-4180-quoted as needed.
    const csv = toCsv(['name'], [['=HYPERLINK("evil")'], ['+1+2'], ['@SUM(A1)'], ['safe']]);
    expect(csv).toBe('name\r\n"\'=HYPERLINK(""evil"")"\r\n\'+1+2\r\n\'@SUM(A1)\r\nsafe');
  });

  it('leaves negative numbers numeric (no apostrophe guard on number cells)', () => {
    expect(toCsv(['m'], [[-2.5]])).toBe('m\r\n-2.5');
  });
});
