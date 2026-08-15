import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Result, ResultSampling } from '../../src/api/runs';
import { SampledRunNotice, SampledTag } from '../../src/components/results/sampling';
import { isSampled, samplingSummary } from '../../src/components/results/samplingFormat';

function record(overrides: Partial<ResultSampling> = {}): ResultSampling {
  return {
    strategy: 'head',
    requested_rows: 100,
    rows: 100,
    total_rows: 5000,
    sampled: true,
    ...overrides,
  };
}

function result(
  sampling: ResultSampling | null,
  status: Result['status'] = 'pass',
): Pick<Result, 'sampling' | 'status'> {
  return { sampling, status };
}

describe('SampledTag', () => {
  it('renders only when the read genuinely was a sample', () => {
    // `sampled: false` is the case that matters most: a "sample" of 100 rows from
    // a 40-row file covered everything, so its verdict is complete. Badging it
    // would put a caveat on every small target and teach the reader to skip past
    // the caveat that does mean something (#424's overclaim lesson).
    const { rerender } = render(<SampledTag sampling={record({ sampled: false })} />);
    expect(screen.queryByTestId('sampled-tag')).not.toBeInTheDocument();

    rerender(<SampledTag sampling={null} />);
    expect(screen.queryByTestId('sampled-tag')).not.toBeInTheDocument();

    rerender(<SampledTag sampling={undefined} />);
    expect(screen.queryByTestId('sampled-tag')).not.toBeInTheDocument();

    rerender(<SampledTag sampling={record()} />);
    expect(screen.getByTestId('sampled-tag')).toBeInTheDocument();
  });

  it('does NOT derive sampled-ness from rows vs total_rows', () => {
    // A partial read that the backend nonetheless reports as complete must be
    // trusted — `sampled` is the field, and re-deriving it here would put the
    // frontend's guess above the runner's own record of what it read.
    render(<SampledTag sampling={record({ rows: 10, total_rows: 5000, sampled: false })} />);
    expect(screen.queryByTestId('sampled-tag')).not.toBeInTheDocument();
  });

  it('actually hands the summary to the tooltip a reader hovers', async () => {
    // The wiring test. `samplingSummary`'s wording is pinned below as a value;
    // this proves the badge really shows it rather than an empty overlay — the
    // failure mode a `getAttribute('title')` assertion hides by reading
    // `undefined` and passing.
    const user = userEvent.setup();
    const sampling = record({ strategy: 'random', rows: 100_000, total_rows: 5_000_000, seed: 7 });
    render(<SampledTag sampling={sampling} />);

    await user.hover(screen.getByTestId('sampled-tag'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(samplingSummary(sampling));
  });
});

describe('samplingSummary', () => {
  it('describes the coverage, the strategy and the seed', () => {
    const tip = samplingSummary(
      record({ strategy: 'random', rows: 100_000, total_rows: 5_000_000, seed: 7 }),
    );
    expect(tip).toContain('random');
    expect(tip).toContain('100,000 of 5,000,000 rows');
    expect(tip).toContain('seed 7');
    expect(tip).toContain('not the whole dataset');
  });

  it('says what it knows when the population was never counted', () => {
    // A head sample stops reading at the cap rather than pay for a count, so
    // `total_rows` is legitimately null — the summary must not invent a
    // denominator or print "of null".
    const tip = samplingSummary(record({ rows: 100, total_rows: null }));
    expect(tip).toContain('100 rows');
    expect(tip).not.toContain('null');
    expect(tip).not.toContain('100 of');
  });

  it('names the requested cap only when it differs from what arrived', () => {
    expect(samplingSummary(record({ requested_rows: 100, rows: 100 }))).not.toContain('asked for');
    // A head sample that hit EOF early got fewer rows than it asked for; that
    // gap is exactly what tells a reader the cap was not the binding constraint.
    expect(samplingSummary(record({ requested_rows: 100_000, rows: 4_312 }))).toContain(
      'asked for 100,000',
    );
  });

  it('omits a seed the record does not carry', () => {
    expect(samplingSummary(record({ strategy: 'head' }))).not.toContain('seed');
  });
});

describe('isSampled', () => {
  it('is true only for an explicit sampled record', () => {
    expect(isSampled(result(record()))).toBe(true);
    expect(isSampled(result(record({ sampled: false })))).toBe(false);
    expect(isSampled(result(null))).toBe(false);
  });
});

describe('SampledRunNotice', () => {
  it('renders nothing when no result was sampled', () => {
    render(<SampledRunNotice results={[result(null), result(record({ sampled: false }))]} />);
    expect(screen.queryByTestId('sampled-run-notice')).not.toBeInTheDocument();
  });

  it('counts rather than generalises when only some checks were sampled', () => {
    // Within one run a volume monitor pushes its COUNT(*) down and is exact
    // while the expectations beside it saw a sample — "this run was sampled"
    // would be wrong about half the table.
    render(<SampledRunNotice results={[result(record()), result(null), result(record())]} />);
    expect(screen.getByTestId('sampled-run-notice')).toHaveTextContent(
      '2 of 3 checks ran on a sample',
    );
  });

  it('says so plainly when every check was sampled', () => {
    render(<SampledRunNotice results={[result(record()), result(record())]} />);
    expect(screen.getByTestId('sampled-run-notice')).toHaveTextContent(
      'Every check ran on a sample',
    );
  });

  it('counts EVALUATED results only, like the "Checks passed" stat beside it', () => {
    // A skip/error never evaluated anything and its `sampling` is always null, so
    // counting it would put a second denominator for one run in one header — and
    // would let one skipped check permanently downgrade the wording below.
    render(
      <SampledRunNotice
        results={[result(record()), result(null, 'skip'), result(null, 'error')]}
      />,
    );
    expect(screen.getByTestId('sampled-run-notice')).toHaveTextContent(
      'Every check ran on a sample',
    );
  });

  it('a single skip does not suppress the "Every check" wording', () => {
    // The regression the denominator bug caused: two sampled checks and one skip
    // read as "2 of 3", quietly weakening the caveat because of something
    // unrelated to sampling.
    render(
      <SampledRunNotice results={[result(record()), result(record()), result(null, 'skip')]} />,
    );
    expect(screen.getByTestId('sampled-run-notice')).not.toHaveTextContent('2 of 3');
  });

  it('spells out that a pass on a sample is not a pass on the dataset', () => {
    render(<SampledRunNotice results={[result(record())]} />);
    expect(screen.getByTestId('sampled-run-notice')).toHaveTextContent(
      /can pass on a sample and still have failing rows outside it/,
    );
  });
});
