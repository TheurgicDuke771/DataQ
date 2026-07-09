import { Alert, Button, Descriptions, type FormInstance, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';

import type { ResultStatus } from '../../api/runs';
import { type CheckDryRunResult, dryRunCheck, targetString } from '../../api/suites';
import { RESULT_STATUS_COLORS } from '../results/resultsFormat';
import { ScalarValue } from '../results/ScalarValue';
import { buildCheckPayload } from './checkForm';
import { errorMessage } from '../../utils/errors';

/**
 * Inline "preview before saving" affordance for the check editor: runs the
 * in-progress check against the suite's live target via the dry-run API
 * (`POST /suites/{id}/checks/dryrun`) and shows the severity outcome — without
 * persisting a Run/Result. Shared by the create page (`CheckNew`) and the edit
 * page (`CheckEdit`). The target is resolved server-side from the suite's own run
 * target (#215/#532), so nothing about it is sent from here.
 *
 * Works on every datasource with a runner — Snowflake, Unity Catalog, and flat
 * files (ADLS / S3 / local) (#532). The button is disabled (with a reason) until
 * an expectation is picked and the suite has a run target; everything else (no
 * credential, unreachable datasource, batch file not landed yet) comes back as a
 * clean 4xx/502 from the API and renders in the alert.
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

  // Clear a previous preview when the expectation changes — on the edit page
  // the picker switches type in-place (DryRunPreview stays mounted), so without
  // this the old result would linger, misattributed to the new expectation.
  // Render-phase reset (React's "adjust state when a prop changes" pattern)
  // rather than an effect, so the stale result never paints.
  const [prevExpectation, setPrevExpectation] = useState(expectationType);
  if (expectationType !== prevExpectation) {
    setPrevExpectation(expectationType);
    setState({ status: 'idle' });
  }

  // A suite is previewable once it has a run target of any shape — a SQL/UC
  // table, a literal flat-file path, or a flat-file batch pattern (#532). The
  // concrete target (incl. UC catalog + batch file resolution) is resolved
  // server-side; a batch whose file hasn't landed comes back as a clean 422.
  const hasTarget =
    !!targetString(target, 'table') ||
    !!targetString(target, 'path') ||
    !!targetString(target, 'pattern');

  const disabledReason = !expectationType
    ? 'Pick an expectation to preview it.'
    : !hasTarget
      ? 'Set a table or file target on the suite to preview against live data.'
      : undefined;

  const run = async () => {
    if (!expectationType || !hasTarget) return;
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
      });
      setState({ status: 'ok', result });
    } catch (err) {
      setState({ status: 'error', error: errorMessage(err) });
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
        <Alert type="error" showIcon title="Dry-run failed" description={state.error} />
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
          children: <ScalarValue value={result.observed_value} />,
        },
        {
          key: 'expected',
          label: 'Expected',
          children: <ScalarValue value={result.expected_value} />,
        },
      ]}
    />
  );
}
