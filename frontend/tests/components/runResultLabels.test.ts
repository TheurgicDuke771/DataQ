import { describe, expect, it } from 'vitest';

import { checkLabel, noResultsMessage } from '../../src/components/results/runResultLabels';

describe('noResultsMessage', () => {
  it('does not call a run that completed incomplete', () => {
    // A check's results are deleted with the check, so a finished run can have none left.
    const message = noResultsMessage('succeeded');
    expect(message).toMatch(/This run completed/);
    expect(message).toMatch(/removed when the check is deleted/);
    expect(message).not.toMatch(/did not complete/);
  });

  it('says a queued or running run has not finished yet', () => {
    expect(noResultsMessage('queued')).toMatch(/has not finished/);
    expect(noResultsMessage('running')).toMatch(/has not finished/);
  });

  it('keeps "did not complete" for a run that failed or was cancelled', () => {
    expect(noResultsMessage('failed')).toMatch(/did not complete/);
    expect(noResultsMessage('cancelled')).toMatch(/did not complete/);
  });
});

describe('checkLabel', () => {
  it('uses the check name when the check exists', () => {
    expect(checkLabel('Orders not null', 'abcdef0123456789', true)).toBe('Orders not null');
  });

  it('says a check missing from a loaded list was deleted, not its raw id', () => {
    expect(checkLabel(undefined, 'abcdef0123456789', true)).toBe('Deleted check (abcdef01)');
  });

  it('claims nothing when the check list could not be loaded', () => {
    // Every check is "missing" then; calling them all deleted would be false.
    expect(checkLabel(undefined, 'abcdef0123456789', false)).toBe('abcdef01');
  });
});
