import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type ColumnTrace,
  type LineageEdge,
  type LineageNode,
  traceColumn,
} from '../../src/api/assets';
import { ColumnLineagePanel } from '../../src/components/assets/ColumnLineagePanel';

vi.mock('../../src/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/assets')>();
  return { ...actual, traceColumn: vi.fn() };
});

const mockTrace = vi.mocked(traceColumn);

const CENTER = 'aaaaaaaa-0000-0000-0000-000000000001';
const OPEN = 'aaaaaaaa-0000-0000-0000-000000000002';
const HIDDEN = 'aaaaaaaa-0000-0000-0000-000000000003';

const node = (overrides: Partial<LineageNode> & { id: string }): LineageNode => ({
  namespace: 'unitycatalog://ws',
  name: 'dataq_retail.silver.feedback',
  env: 'dev',
  is_monitored: true,
  depth: 1,
  ...overrides,
});

function renderPanel(edges: LineageEdge[], nodes: LineageNode[] = [node({ id: OPEN })]) {
  return render(
    <ColumnLineagePanel
      centerId={CENTER}
      centerName="dataq_retail.gold.feedback_sentiment"
      nodes={nodes}
      edges={edges}
    />,
  );
}

beforeEach(() => {
  mockTrace.mockReset();
});

describe('ColumnLineagePanel (#901)', () => {
  it('renders the column pairs of an accessible direct edge', () => {
    renderPanel([
      {
        source: CENTER,
        target: OPEN,
        columns: [
          ['comment', 'sentiment'],
          ['customer_id', 'customer_id'],
        ],
        column_coverage: 'recorded',
      },
    ]);
    expect(screen.getByText('comment → sentiment')).toBeInTheDocument();
    expect(screen.getByText('customer_id → customer_id')).toBeInTheDocument();
    expect(screen.getByText('2 column links')).toBeInTheDocument();
    expect(screen.getByTestId('column-edge')).toBeInTheDocument();
  });

  it('degrades a dangling endpoint to a placeholder label, never crashes', () => {
    renderPanel([{ source: CENTER, target: HIDDEN, columns: [['comment', 'sentiment']] }], []);
    expect(screen.getByText(/Unknown asset/)).toBeInTheDocument();
    expect(screen.getByText('comment → sentiment')).toBeInTheDocument();
  });

  it('says so when there are no direct edges at all', () => {
    renderPanel([{ source: OPEN, target: HIDDEN, columns: [['a', 'b']] }]); // not direct
    expect(screen.getByText(/No direct lineage edges recorded/)).toBeInTheDocument();
  });
});

describe('ColumnLineagePanel coverage honesty (#1710)', () => {
  it.each([
    ['none_recorded', /not proof the columns are unrelated/],
    ['unavailable', /column-lineage read failed/],
    ['unknown', /has not refreshed since this was tracked/],
    ['not_captured', /never carries column detail/],
  ] as const)('explains a %s edge instead of implying no column dependency', (coverage, text) => {
    renderPanel([{ source: OPEN, target: CENTER, columns: null, column_coverage: coverage }]);
    expect(screen.getByTestId('column-edge-uncovered')).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByText(/No column-level lineage recorded/)).toBeInTheDocument();
  });

  it('reads a pre-#1710 edge (no field) as unknown, never as none recorded', () => {
    renderPanel([{ source: OPEN, target: CENTER }]);
    expect(screen.getByText(/has not refreshed since this was tracked/)).toBeInTheDocument();
  });
});

const trace = (overrides: Partial<ColumnTrace> = {}): ColumnTrace => ({
  asset_id: CENTER,
  column: 'customer_id',
  upstream: [{ asset_id: OPEN, column: 'customer_id', depth: 1 }],
  downstream: [],
  hops: [],
  gaps: [],
  origins: [{ asset_id: OPEN, column: 'customer_id', depth: 1, confirmed: true }],
  upstream_status: 'traced',
  downstream_status: 'no_table_lineage',
  truncated: false,
  complete: true,
  assets: [
    { id: OPEN, namespace: 'n', name: 'dataq_retail.raw.feedback', env: null, is_monitored: false },
    { id: CENTER, namespace: 'n', name: 'dataq_retail.gold.x', env: null, is_monitored: true },
  ],
  qualified_by: [],
  ...overrides,
});

describe('column trace (#1710)', () => {
  it('traces the typed column and shows its origin', async () => {
    mockTrace.mockResolvedValue(trace());
    const user = userEvent.setup();
    renderPanel([]);
    await user.type(screen.getByRole('combobox', { name: 'Column to trace' }), 'customer_id');
    await user.click(screen.getByRole('button', { name: 'Trace' }));
    expect(mockTrace).toHaveBeenCalledWith(CENTER, 'customer_id', 'both');
    expect(await screen.findByText('feedback.customer_id')).toBeInTheDocument();
    expect(screen.queryByText('Partial trace')).not.toBeInTheDocument();
    expect(screen.getByText(/no lineage edges this way/)).toBeInTheDocument();
  });

  it('warns on a partial trace and lists the gaps and unconfirmed origins', async () => {
    mockTrace.mockResolvedValue(
      trace({
        complete: false,
        origins: [{ asset_id: OPEN, column: 'customer_id', depth: 1, confirmed: false }],
        gaps: [{ upstream_asset_id: HIDDEN, downstream_asset_id: OPEN, coverage: 'not_captured' }],
        qualified_by: ["warehouse lineage on 'sf' is coarse"],
      }),
    );
    const user = userEvent.setup();
    renderPanel([]);
    await user.type(screen.getByRole('combobox', { name: 'Column to trace' }), 'x');
    await user.click(screen.getByRole('button', { name: 'Trace' }));
    expect(await screen.findByText('Partial trace')).toBeInTheDocument();
    expect(screen.getByText('may have further upstream')).toBeInTheDocument();
    expect(screen.getByText(/Unknown asset → feedback: not captured/)).toBeInTheDocument();
    expect(screen.getByText(/is coarse/)).toBeInTheDocument();
  });

  it('says a truncated walk hit its cap', async () => {
    mockTrace.mockResolvedValue(trace({ complete: false, truncated: true }));
    const user = userEvent.setup();
    renderPanel([]);
    await user.type(screen.getByRole('combobox', { name: 'Column to trace' }), 'x');
    await user.click(screen.getByRole('button', { name: 'Trace' }));
    expect(await screen.findByText(/hit its depth\/size cap/)).toBeInTheDocument();
  });

  it('surfaces a failed trace as an error', async () => {
    mockTrace.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPanel([]);
    await user.type(screen.getByRole('combobox', { name: 'Column to trace' }), 'x');
    await user.click(screen.getByRole('button', { name: 'Trace' }));
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it('keeps Trace disabled until a column is entered', () => {
    renderPanel([]);
    expect(screen.getByRole('button', { name: 'Trace' })).toBeDisabled();
  });
});
