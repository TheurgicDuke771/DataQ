import { Alert, Button, Descriptions, type FormInstance, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';

import type { ResultStatus } from '../../api/runs';
import { type CheckDryRunResult, dryRunCheck } from '../../api/suites';
import { RESULT_STATUS_COLORS } from '../results/resultsFormat';
import { buildCheckPayload } from './checkForm';

/**
 * Inline "preview before saving" affordance for the check editor: runs the
 * in-progress check against the suite's live target via the dry-run API
 * (`POST /suites/{id}/checks/dryrun`) and shows the severity outcome — without
 * persisting a Run/Result. Shared by the create page (`CheckNew`) and the edit
 * drawer (`CheckDrawer`); both pass the suite's run target (#215) so the same
 * `table`/`schema` the run would use is previewed.
 *
 * v1 backend limits (surfaced as the API's error message): dry-run needs a
 * table target and a Snowflake connection. The button is disabled (with a
 * reason) until an expectation is picked and the suite has a table target;
 * everything else (no credential, unreachable warehouse, wrong datasource) comes
 * back as a clean error from the API and renders in the alert.
 */
export function DryRunPreview({
  suiteId,
  expectationType,
  target,
  form,
}: {
  suiteId: string;
  expectationType: string | undefined;
  target: Record<string, unknown> | null;
  form: FormInstance;
}) {
  const [state, setState] = useState<
    | { status: 'idle' }
    | { status: 'running' }
    | { status: 'ok'; result: CheckDryRunResult }
    | { status: 'error'; error: string }
  >({ status: 'idle' });

  // Clear a previous preview when the expectation changes — in the edit drawer
  // the picker switches type in-place (DryRunPreview stays mounted), so without
  // this the old result would linger, misattributed to the new expectation.
  // Render-phase reset (React's "adjust state when a prop changes" pattern)
  // rather than an effect, so the stale result never paints.
  const [prevExpectation, setPrevExpectation] = useState(expectationType);
  if (expectationType !== prevExpectation) {
    setPrevExpectation(expectationType);
    setState({ status: 'idle' });
  }

  const table = typeof target?.table === 'string' ? target.table : undefined;
  const schema = typeof target?.schema === 'string' ? target.schema : null;

  const disabledReason = !expectationType
    ? 'Pick an expectation to preview it.'
    : !table
      ? 'Set a table target on the suite to preview against live data.'
      : undefined;

  const run = async () => {
    if (!expectationType || !table) return;
    setState({ status: 'running' });
    try {
      // Reuse the create/update payload shaping so the preview runs exactly the
      // config (and thresholds) the saved check would — name is irrelevant here.
      const payload = buildCheckPayload({
        ...form.getFieldsValue(true),
        expectation_type: expectationType,
      });
      const result = await dryRunCheck(suiteId, {
        expectation_type: expectationType,
        config: payload.config,
        warn_threshold: payload.warn_threshold,
        fail_threshold: payload.fail_threshold,
        critical_threshold: payload.critical_threshold,
        table,
        schema,
      });
      setState({ status: 'ok', result });
    } catch (err) {
      setState({ status: 'error', error: err instanceof Error ? err.message : 'unknown error' });
    }
  };

  return (
    <Flex vertical gap={8}>
      <Flex gap={12} align="center">
        <Button onClick={run} loading={state.status === 'running'} disabled={!!disabledReason}>
          Dry-run preview
        </Button>
        {disabledReason && <Typography.Text type="secondary">{disabledReason}</Typography.Text>}
      </Flex>
      {state.status === 'ok' && <DryRunResultView result={state.result} />}
      {state.status === 'error' && (
        <Alert type="error" showIcon message="Dry-run failed" description={state.error} />
      )}
    </Flex>
  );
}

function DryRunResultView({ result }: { result: CheckDryRunResult }) {
  return (
    <Descriptions
      size="small"
      bordered
      column={1}
      styles={{ label: { width: 140 } }}
      items={[
        {
          key: 'status',
          label: 'Result',
          children: (
            <Tag color={RESULT_STATUS_COLORS[result.status as ResultStatus]}>{result.status}</Tag>
          ),
        },
        {
          key: 'metric',
          label: 'Metric',
          children: result.metric_value === null ? '—' : result.metric_value,
        },
        {
          key: 'observed',
          label: 'Observed',
          children: result.observed_value ? (
            <Typography.Text code>{JSON.stringify(result.observed_value)}</Typography.Text>
          ) : (
            '—'
          ),
        },
        {
          key: 'expected',
          label: 'Expected',
          children: result.expected_value ? (
            <Typography.Text code>{JSON.stringify(result.expected_value)}</Typography.Text>
          ) : (
            '—'
          ),
        },
      ]}
    />
  );
}
