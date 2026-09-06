import { Alert, Button, Card, Flex, InputNumber, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  getScoringWeights,
  putScoringWeights,
  resetScoringWeights,
  type ScoringWeights,
} from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { fetchFailure } from '../../utils/errors';

type Draft = { warn: number | null; fail: number | null; critical: number | null };

const TIERS: { key: keyof Draft; label: string }[] = [
  { key: 'warn', label: 'Warn' },
  { key: 'fail', label: 'Fail' },
  { key: 'critical', label: 'Critical' },
];

function draftOf(w: ScoringWeights): Draft {
  return { warn: w.warn, fail: w.fail, critical: w.critical };
}

/** Client-side mirror of the server rule so the reason shows before a round-trip. */
function invalidReason(d: Draft): string | null {
  const { warn, fail, critical } = d;
  if (warn === null || fail === null || critical === null) return 'Every weight needs a value.';
  if (warn < 0) return 'Weights cannot be negative.';
  if (!(warn <= fail && fail <= critical)) return 'Keep the order warn ≤ fail ≤ critical.';
  if (critical <= 0) return 'Critical must be above zero — it normalises the score.';
  return null;
}

/** The health-score penalty weights. Scores are computed on read, so a save recolours every score. */
export function ScoringPanel() {
  const { state } = useAsyncData(() => getScoringWeights());
  const [saved, setSaved] = useState<ScoringWeights | null>(null);
  const [edited, setEdited] = useState<Draft | null>(null);
  const { run, loading } = useAsyncAction('Could not save the scoring weights');
  const current = saved ?? (state.status === 'ok' ? state.data : null);
  const draft = edited ?? (current ? draftOf(current) : null);

  const apply = (next: ScoringWeights) => {
    setSaved(next);
    setEdited(null);
  };
  const reason = draft ? invalidReason(draft) : null;
  const dirty =
    current !== null &&
    draft !== null &&
    (draft.warn !== current.warn ||
      draft.fail !== current.fail ||
      draft.critical !== current.critical);

  const onSave = () =>
    void run(async () => {
      if (!draft) return;
      apply(
        await putScoringWeights({
          warn: draft.warn as number,
          fail: draft.fail as number,
          critical: draft.critical as number,
        }),
      );
    });
  const onReset = () => void run(async () => apply(await resetScoringWeights()));

  return (
    <Card title="Scoring" size="small">
      {state.status === 'loading' && !current && (
        <Typography.Text type="secondary">Loading…</Typography.Text>
      )}
      {state.status === 'error' && !current && (
        <Alert
          type="error"
          showIcon
          title="Could not load the scoring weights"
          description={fetchFailure(state.error).message}
        />
      )}
      {current && draft && (
        <Flex vertical gap={12}>
          <Flex align="center" gap={12} wrap>
            <Typography.Text strong>Health-score weights</Typography.Text>
            <Tag color={current.is_default ? 'default' : 'blue'}>
              {current.is_default ? 'Defaults' : 'Customised'}
            </Tag>
          </Flex>
          <Space wrap size="middle">
            {TIERS.map(({ key, label }) => (
              <Space key={key} orientation="vertical" size={2}>
                <Typography.Text type="secondary">{label}</Typography.Text>
                <InputNumber
                  aria-label={`${label} weight`}
                  min={0}
                  max={100}
                  step={0.1}
                  value={draft[key]}
                  disabled={loading}
                  onChange={(v) => setEdited({ ...draft, [key]: v })}
                />
              </Space>
            ))}
            <Space orientation="vertical" size={2}>
              <Typography.Text type="secondary">Pass</Typography.Text>
              <InputNumber value={0} disabled aria-label="Pass weight" />
            </Space>
          </Space>
          {reason && <Alert type="warning" showIcon title={reason} />}
          <Space>
            <Button type="primary" onClick={onSave} disabled={!dirty || !!reason} loading={loading}>
              Save
            </Button>
            <Button onClick={onReset} disabled={current.is_default || loading}>
              Reset to defaults
            </Button>
          </Space>
          <Typography.Text type="secondary">
            Defaults {current.defaults.warn} / {current.defaults.fail} / {current.defaults.critical}
            . Critical doubles as the normaliser, so an all-fail suite scores{' '}
            {Math.round(100 * (1 - current.fail / current.critical))} with the current weights.
          </Typography.Text>
          <Alert
            type="info"
            showIcon
            title="Scores are computed when read, never stored"
            description="Saving recolours every score at once — dashboard cards and deltas, suite ranking, asset scorecards, and runs from before the change. The audit log is the only record of the step."
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Changes are audited (see <Link to="/admin/compliance">Compliance</Link>).
            {current.updated_at &&
              ` Last changed ${formatTimestamp(current.updated_at)}${current.updated_by ? ` by ${current.updated_by}` : ''}.`}
          </Typography.Text>
        </Flex>
      )}
    </Card>
  );
}
