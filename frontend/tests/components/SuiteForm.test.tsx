import { App as AntApp } from 'antd';
import { AxiosError, AxiosHeaders } from 'axios';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Connection } from '../../src/api/connections';
import { createSuite, previewBatchTarget, type Suite, updateSuite } from '../../src/api/suites';
import { SuiteForm } from '../../src/components/suites/SuiteForm';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    createSuite: vi.fn(),
    updateSuite: vi.fn(),
    previewBatchTarget: vi.fn(),
  };
});

const mockCreate = vi.mocked(createSuite);
const mockUpdate = vi.mocked(updateSuite);
const mockPreview = vi.mocked(previewBatchTarget);

/** An axios error shaped like what the API client actually rejects with — the
 *  response interceptor has already swapped the envelope message onto
 *  `error.message` by the time a caller sees it (mirrors utils/errors.test.ts). */
function batchPreviewFailure(
  status: number,
  code: string,
  message: string,
  detail: Record<string, unknown> = {},
): AxiosError {
  const err = new AxiosError(message);
  err.response = {
    status,
    statusText: '',
    data: { error: { code, message, detail } },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return err;
}

const adlsConnection: Connection = {
  id: 'conn-adls',
  name: 'adls-prod',
  type: 'adls_gen2',
  env: 'prod',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const snowflakeConnection: Connection = {
  id: 'conn-sf',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function suite(overrides: Partial<Suite> = {}): Suite {
  return {
    id: 's1',
    name: 'logistics-suite',
    description: null,
    connection_id: 'conn-adls',
    target: null,
    created_by: 'u1',
    ...overrides,
  };
}

function renderForm(props: Partial<Parameters<typeof SuiteForm>[0]> = {}) {
  return render(
    <AntApp>
      <SuiteForm
        connections={[adlsConnection, snowflakeConnection]}
        onSaved={vi.fn()}
        onCancel={vi.fn()}
        {...props}
      />
    </AntApp>,
  );
}

async function pickConnection(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getByLabelText('Connection'));
  await user.click(await screen.findByText(name));
}

afterEach(() => {
  vi.clearAllMocks();
  // `clearAllMocks` clears recorded calls but NOT queued `…Once` implementations,
  // and the preview is debounced — a test can legitimately finish before its
  // 400ms timer fires, leaving an unconsumed queue entry that the NEXT test's
  // mount call would pick up instead of its own. Drain it explicitly.
  mockPreview.mockReset();
});

describe('SuiteForm — flat-file batch target (#1180)', () => {
  it('offers the single/batch mode toggle only for flat-file connections', async () => {
    const user = userEvent.setup();
    renderForm();

    await pickConnection(user, /adls-prod/);
    expect(await screen.findByText('Batch pattern')).toBeInTheDocument();

    // Switching to a warehouse connection drops the toggle — the SQL target
    // shape has no batch concept.
    await pickConnection(user, /sf-dev/);
    expect(screen.queryByText('Batch pattern')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Table')).toBeInTheDocument();
  });

  it('serializes prefix/pattern/latest-strategy batch fields into the submitted target', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));

    await user.type(
      screen.getByLabelText('Prefix (optional)'),
      'adls_flatfile/logistics_tracking/',
    );
    // fireEvent.change, not user.type — userEvent's keyboard parser treats
    // `[`/`]` as special-key syntax, which a batch pattern regex is full of.
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'tracking_events_([a-z_]+)\\.csv' },
    });
    // Strategy defaults to 'latest' — leave it as-is.

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0]).toMatchObject({
      connection_id: 'conn-adls',
      target: {
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      },
    });
    // 'latest' never carries a batch key.
    expect(mockCreate.mock.calls[0][0].target).not.toHaveProperty('batch');
  });

  it('serializes a specific-strategy batch target with its batch key', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));
    // fireEvent.change, not user.type — userEvent's keyboard parser treats
    // `[`/`]` as special-key syntax, which a batch pattern regex is full of.
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'tracking_events_([a-z_]+)\\.csv' },
    });
    await selectOption(user, 'Specific batch key', { index: 1 });
    await user.type(await screen.findByLabelText('Batch key'), 'ready');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0].target).toEqual({
      pattern: 'tracking_events_([a-z_]+)\\.csv',
      strategy: 'specific',
      batch: 'ready',
    });
  });

  it('flags a specific-strategy pattern with no capture group before it hits the API', async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));
    // No parentheses at all — can't extract a batch key.
    await user.type(screen.getByLabelText('Filename pattern (regex)'), 'tracking_events_ready.csv');
    await selectOption(user, 'Specific batch key', { index: 1 });
    await user.type(await screen.findByLabelText('Batch key'), 'ready');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    expect(await screen.findByText(/needs a capture group/)).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('reopens an existing batch target in batch mode with its fields populated', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    // This target is already `active` (a suiteId + pattern) on first render, so
    // the 400ms preview debounce (#1193) WILL fire — normally cancelled by
    // unmount before it does, but that's a race, not a guarantee (#1254: an
    // unmocked call resolves `undefined` from the bare `vi.fn()`, and `.then()`
    // on `undefined` throws if the timer wins). Mock it like the "batch preview
    // hint" describe block below does, since this test isn't asserting on the
    // preview outcome anyway.
    mockPreview.mockResolvedValue('adls_flatfile/logistics_tracking/irrelevant.csv');
    renderForm({
      suite: suite({
        target: {
          prefix: 'adls_flatfile/logistics_tracking/',
          pattern: 'tracking_events_([a-z_]+)\\.csv',
          strategy: 'latest',
        },
      }),
    });

    await waitFor(() =>
      expect(screen.getByLabelText('Filename pattern (regex)')).toHaveValue(
        'tracking_events_([a-z_]+)\\.csv',
      ),
    );
    expect(screen.getByLabelText('Prefix (optional)')).toHaveValue(
      'adls_flatfile/logistics_tracking/',
    );
    expect(screen.getByRole('radio', { name: 'Batch pattern' })).toBeChecked();
    // The literal single-file field never mounted in this mode.
    expect(screen.queryByLabelText('File path')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'logistics-suite',
      description: null,
      target: {
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      },
    });
  });

  it('reopens an existing specific-strategy batch target with its batch key', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    // Same #1254 debounce race as the test above: this target is active from
    // first render, so mock the preview call the debounce will make.
    mockPreview.mockResolvedValue('adls_flatfile/logistics_tracking/irrelevant.csv');
    renderForm({
      suite: suite({
        target: {
          pattern: 'tracking_events_([a-z_]+)\\.csv',
          strategy: 'specific',
          batch: 'ready',
        },
      }),
    });

    await waitFor(() => expect(screen.getByLabelText('Batch key')).toHaveValue('ready'));

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'logistics-suite',
      description: null,
      target: {
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'specific',
        batch: 'ready',
      },
    });
  });

  it('falls back to "latest" when the stored strategy is malformed, without crashing', async () => {
    // The suite target is untyped JSONB (a hand-edited row, or a value from an
    // older schema) — a stray `strategy` must not prefill the Select with an
    // option that doesn't exist (the same class of bug asFileFormat guards
    // against for file_format, mirrored here by asBatchStrategy).
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    // Same #1254 debounce race: 'weekly' falls back to 'latest', which is
    // still an active target, so the debounce still fires.
    mockPreview.mockResolvedValue('adls_flatfile/logistics_tracking/irrelevant.csv');
    renderForm({
      suite: suite({
        target: {
          pattern: 'tracking_events_([a-z_]+)\\.csv',
          strategy: 'weekly', // not a real strategy
        },
      }),
    });

    await waitFor(() =>
      expect(screen.getByLabelText('Filename pattern (regex)')).toHaveValue(
        'tracking_events_([a-z_]+)\\.csv',
      ),
    );
    // Falls back to 'latest' — the Specific-only Batch key field stays hidden.
    expect(screen.queryByLabelText('Batch key')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'logistics-suite',
      description: null,
      target: {
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      },
    });
  });

  it('leaves single-file mode unchanged for a literal path target', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    // Default mode is 'single' — batch fields never render without opting in.
    expect(screen.queryByLabelText('Filename pattern (regex)')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('File path'), 'container/path/to/data.csv');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0].target).toEqual({ path: 'container/path/to/data.csv' });
  });
});

/** Assert the hint stops claiming a resolved path *before* the 400ms debounce
 *  fires. The window matters: once the timer fires the component sets `loading`
 *  on its own, so an unbounded `findByText`/`waitFor` would pass whether or not
 *  render drops a stale answer. A timer can fire late but never early, so the
 *  ceiling is what makes this discriminating; the floor only has to cover one
 *  antd `useWatch` flush + re-render (sub-millisecond in practice). */
async function expectStaleAnswerDropped(path: string) {
  await waitFor(
    () => {
      expect(screen.queryByText(path)).not.toBeInTheDocument();
      expect(screen.queryByText(/Resolves to:/)).not.toBeInTheDocument();
      expect(screen.getByText('Checking the live listing…')).toBeInTheDocument();
    },
    { timeout: 150, interval: 10 },
  );
}

describe('SuiteForm — batch preview hint (#1193)', () => {
  it('shows "Resolves to: <path>" once the live preview resolves, and re-fetches on a field change', async () => {
    mockPreview.mockResolvedValueOnce('orders/orders_20260601.csv');
    renderForm({
      suite: suite({
        id: 's1',
        target: {
          prefix: 'orders/',
          pattern: 'orders_(\\d+)\\.csv',
          strategy: 'latest',
        },
      }),
    });

    await waitFor(() =>
      expect(screen.getByLabelText('Filename pattern (regex)')).toHaveValue('orders_(\\d+)\\.csv'),
    );
    await waitFor(() => expect(mockPreview).toHaveBeenCalledWith('s1', expect.anything()), {
      timeout: 2000,
    });
    expect(mockPreview).toHaveBeenCalledWith('s1', {
      pattern: 'orders_(\\d+)\\.csv',
      strategy: 'latest',
      prefix: 'orders/',
    });
    expect(await screen.findByText('Resolves to:')).toBeInTheDocument();
    expect(await screen.findByText('orders/orders_20260601.csv')).toBeInTheDocument();

    // Editing the pattern re-requests a fresh preview for the new spec.
    mockPreview.mockResolvedValueOnce('orders/orders_20260602.csv');
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'orders_(\\d+)_v2\\.csv' },
    });
    expect(await screen.findByText('orders/orders_20260602.csv')).toBeInTheDocument();
    expect(mockPreview).toHaveBeenLastCalledWith('s1', {
      pattern: 'orders_(\\d+)_v2\\.csv',
      strategy: 'latest',
      prefix: 'orders/',
    });
  });

  it('shows a friendly "no file matches" hint instead of a raw error on the no-data 422', async () => {
    mockPreview.mockRejectedValueOnce(
      batchPreviewFailure(
        422,
        'batch_preview_no_data',
        'no file currently matches this batch pattern',
      ),
    );
    renderForm({
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'latest' },
      }),
    });

    expect(await screen.findByText('No file currently matches this pattern.')).toBeInTheDocument();
    expect(screen.queryByText(/currently matches this batch pattern/)).not.toBeInTheDocument();
  });

  it('surfaces the classified reason alongside the generic message on a 502', async () => {
    // The 502 message is deliberately generic (the backend never echoes an
    // adapter exception); `detail.reason` is the classified half that says what
    // to fix. Rendering only the message discards it.
    mockPreview.mockRejectedValueOnce(
      batchPreviewFailure(
        502,
        'batch_preview_failed',
        'batch preview could not list the datasource store',
        {
          reason:
            'The datasource rejected the credentials, or a required grant/permission is missing.',
        },
      ),
    );
    renderForm({
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'latest' },
      }),
    });

    expect(
      await screen.findByText(
        'batch preview could not list the datasource store The datasource rejected the ' +
          'credentials, or a required grant/permission is missing.',
      ),
    ).toBeInTheDocument();
  });

  it('still shows the message when the envelope carries no classified reason', async () => {
    mockPreview.mockRejectedValueOnce(
      batchPreviewFailure(422, 'batch_preview_invalid', 'batch prefix lists more than 500000'),
    );
    renderForm({
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'latest' },
      }),
    });

    expect(await screen.findByText('batch prefix lists more than 500000')).toBeInTheDocument();
  });

  it('never calls the preview endpoint in create mode (no suite id yet)', async () => {
    const user = userEvent.setup();
    renderForm();

    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'orders_(\\d+)\\.csv' },
    });

    // Give the 400ms debounce a chance to fire if it were wrongly wired up.
    await new Promise((r) => setTimeout(r, 600));
    expect(mockPreview).not.toHaveBeenCalled();
  });

  it('stays quiet (no call, no hint) until a pattern is entered', async () => {
    // Edit mode (a real suiteId, connection fixed to the default adls-prod
    // fixture) with the target field itself left blank — isolates the
    // "no pattern yet" gate from the "no suiteId yet" one below.
    const user = userEvent.setup();
    renderForm({ suite: suite({ id: 's1', target: null }) });

    await user.click(await screen.findByText('Batch pattern'));
    await user.type(screen.getByLabelText('Prefix (optional)'), 'orders/');

    // Give the 400ms debounce a chance to fire if it were wrongly wired up —
    // no pattern means nothing to preview yet.
    await new Promise((r) => setTimeout(r, 600));
    expect(mockPreview).not.toHaveBeenCalled();
    expect(screen.queryByText(/Resolves to:/)).not.toBeInTheDocument();
  });

  it('drops the previous answer the moment the spec changes, rather than relabelling it', async () => {
    // The hint's whole point is before-you-save confidence, so it must never
    // present an answer about spec A as the resolution of spec B. The 400ms
    // debounce after an edit used to do exactly that: the old path stayed on
    // screen, unlabelled as stale, describing a pattern the form no longer had.
    mockPreview.mockResolvedValueOnce('orders/orders_20260601.csv');
    renderForm({
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'latest' },
      }),
    });
    expect(await screen.findByText('orders/orders_20260601.csv')).toBeInTheDocument();

    // Never resolves: the hint has only the STALE answer to fall back on.
    mockPreview.mockReturnValueOnce(new Promise<string>(() => {}));
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'shipments_(\\d+)\\.csv' },
    });

    await expectStaleAnswerDropped('orders/orders_20260601.csv');
  });

  it('does not re-show a previous answer when the batch key is cleared and re-entered', async () => {
    // The active=false→true flip: emptying the batch key hides the hint, and
    // typing a new one used to bring the OLD batch's path straight back —
    // labelled as the resolution of a batch key it was never asked about.
    mockPreview.mockResolvedValueOnce('orders/orders_20260601.csv');
    renderForm({
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'specific', batch: '20260601' },
      }),
    });
    expect(await screen.findByText('orders/orders_20260601.csv')).toBeInTheDocument();

    // Clearing the key makes the spec un-previewable, so the hint hides entirely.
    fireEvent.change(screen.getByLabelText('Batch key'), { target: { value: '' } });
    await waitFor(() => expect(screen.queryByText(/Resolves to:/)).not.toBeInTheDocument());

    mockPreview.mockReturnValueOnce(new Promise<string>(() => {}));
    fireEvent.change(screen.getByLabelText('Batch key'), { target: { value: '20260615' } });

    await expectStaleAnswerDropped('orders/orders_20260601.csv');
  });

  it('withholds the preview call for a "specific" strategy until a batch key is entered', async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue('orders/orders_ready.csv');
    renderForm({
      // Strategy is 'specific' from the start but with no batch key yet.
      suite: suite({
        id: 's1',
        target: { pattern: 'orders_(\\d+)\\.csv', strategy: 'specific' },
      }),
    });

    await waitFor(() => expect(screen.getByLabelText('Batch key')).toBeInTheDocument());
    // No batch key yet — the debounce must not fire even after it would have.
    await new Promise((r) => setTimeout(r, 600));
    expect(mockPreview).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText('Batch key'), 'ready');

    await waitFor(() =>
      expect(mockPreview).toHaveBeenCalledWith('s1', {
        pattern: 'orders_(\\d+)\\.csv',
        strategy: 'specific',
        batch: 'ready',
      }),
    );
  });
});

// ── sampling authoring (#595/#1325) ──────────────────────────────────

describe('SuiteForm — sampling', () => {
  const ucConnection: Connection = {
    id: 'conn-uc',
    name: 'uc-prod',
    type: 'unity_catalog',
    env: 'prod',
    config: {},
    has_secret: true,
    created_by: 'u1',
  };

  it('offers the section only on datasources that accept a sampling block', async () => {
    // Snowflake pushes every expectation down and never loads rows, so the
    // backend answers a spec there with a 422 — a control whose only outcome is
    // a save error is worse than no control.
    const { rerender } = render(
      <AntApp>
        <SuiteForm
          suite={suite({ connection_id: 'conn-adls', target: { path: 'raw/o.csv' } })}
          connections={[adlsConnection, snowflakeConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    expect(await screen.findByTestId('sampling-enabled')).toBeInTheDocument();

    rerender(
      <AntApp>
        <SuiteForm
          suite={suite({ connection_id: 'conn-sf', target: { table: 'ORDERS' } })}
          connections={[adlsConnection, snowflakeConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    await waitFor(() => expect(screen.queryByTestId('sampling-enabled')).not.toBeInTheDocument());
  });

  it('round-trips a stored sampling block: prefills it, and saves it back', async () => {
    // The round trip is the assertion that matters. #1325's review found the API
    // silently DROPPING this key (`SuiteTarget` is a closed model), which made
    // the whole feature configurable only by writing the database by hand — so
    // an editor that renders the block but loses it on save would reproduce that
    // failure one layer up.
    const user = userEvent.setup();
    const stored = {
      catalog: 'main',
      table: 'orders',
      sampling: { strategy: 'random', rows: 5000, seed: 7 },
    };
    mockUpdate.mockResolvedValue(suite({ connection_id: 'conn-uc', target: stored }));
    render(
      <AntApp>
        <SuiteForm
          suite={suite({ connection_id: 'conn-uc', target: stored })}
          connections={[ucConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );

    // Prefilled from the stored block — the block's presence IS the toggle.
    expect(await screen.findByTestId('sampling-rows')).toHaveValue('5000');
    expect(screen.getByTestId('sampling-seed')).toHaveValue('7');

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('s1', {
        name: 'logistics-suite',
        description: null,
        target: {
          catalog: 'main',
          table: 'orders',
          sampling: { strategy: 'random', rows: 5000, seed: 7 },
        },
      }),
    );
  });

  it('sends no sampling key at all when the suite reads the whole dataset', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    render(
      <AntApp>
        <SuiteForm
          suite={suite({ connection_id: 'conn-adls', target: { path: 'raw/o.csv' } })}
          connections={[adlsConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    await screen.findByTestId('sampling-enabled');

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][1].target).toEqual({ path: 'raw/o.csv' });
  });

  it('hides the seed when the strategy switches to head, and saves the head spec', async () => {
    // A head sample always reads the first rows in storage order and cannot be
    // seeded; the backend 422s a seed there rather than let an author believe
    // otherwise.
    //
    // Scope note, established by mutation-checking this test: it does NOT prove
    // the stale-seed guard. Unmounting the seed `Form.Item` unregisters it, so
    // `validateFields()` stops returning the value and there is no stale seed to
    // drop at this layer — deleting `assembleTarget`'s `strategy === 'random'`
    // condition leaves this test green. That guard is pinned in
    // `suiteTarget.test.ts` ("drops a stale seed when the strategy is head"),
    // which kills the mutant. What this test does prove is the visible
    // behaviour: the field disappears and a head spec is what reaches the API.
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    render(
      <AntApp>
        <SuiteForm
          suite={suite({
            connection_id: 'conn-adls',
            target: { path: 'raw/o.csv', sampling: { strategy: 'random', rows: 100, seed: 7 } },
          })}
          connections={[adlsConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    expect(await screen.findByTestId('sampling-seed')).toHaveValue('7');

    await user.click(screen.getByLabelText('Sample strategy'));
    await user.click(
      await screen.findByText('Head — the first rows in storage order (cheapest)', {
        selector: '.ant-select-item-option-content',
      }),
    );
    await waitFor(() => expect(screen.queryByTestId('sampling-seed')).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][1].target).toEqual({
      path: 'raw/o.csv',
      sampling: { strategy: 'head', rows: 100 },
    });
  });

  it('flags a sampling block with no row count inline instead of saving it', async () => {
    const user = userEvent.setup();
    render(
      <AntApp>
        <SuiteForm
          suite={suite({ connection_id: 'conn-adls', target: { path: 'raw/o.csv' } })}
          connections={[adlsConnection]}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    await screen.findByTestId('sampling-enabled');

    // The label, not the input — antd's button-style radio leaves the input
    // `pointer-events: none` (same idiom as the target-mode toggle above).
    await user.click(screen.getByText('A sample'));
    await screen.findByTestId('sampling-rows');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/whole number of rows between 1 and/)).toBeInTheDocument();
    expect(mockUpdate).not.toHaveBeenCalled();
  });
});
