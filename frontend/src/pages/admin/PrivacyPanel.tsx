import { Alert, Card, Flex, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { getPrivacySettings, type PrivacySettings, putPrivacySettings } from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { fetchFailure } from '../../utils/errors';

/** The zero-sample toggle: on ⇒ failing-row samples are never stored, only aggregates and metrics. */
export function PrivacyPanel() {
  const { state } = useAsyncData(() => getPrivacySettings());
  const [local, setLocal] = useState<PrivacySettings | null>(null);
  const { run, loading } = useAsyncAction('Could not change zero-sample mode');
  const current = local ?? (state.status === 'ok' ? state.data : null);

  const onToggle = (next: boolean) =>
    void run(async () => {
      setLocal(await putPrivacySettings(next));
    });

  return (
    <Card title="Privacy & failing samples" size="small">
      {state.status === 'loading' && !current && (
        <Typography.Text type="secondary">Loading…</Typography.Text>
      )}
      {state.status === 'error' && !current && (
        <Alert
          type="error"
          showIcon
          title="Could not load the privacy settings"
          description={fetchFailure(state.error).message}
        />
      )}
      {current && (
        <Flex vertical gap={12}>
          <Flex align="center" gap={12} wrap>
            <Switch
              checked={current.effective}
              disabled={current.env_forced || loading}
              loading={loading}
              onChange={onToggle}
              aria-label="Zero-sample mode"
            />
            <Typography.Text strong>Zero-sample mode</Typography.Text>
            <Tag color={current.effective ? 'green' : 'default'}>
              {current.effective ? 'On' : 'Off'}
            </Tag>
            {current.source === 'env' && <Tag>Pinned by the environment</Tag>}
          </Flex>
          <Typography.Text type="secondary">
            On: failing-row samples are never stored — results, dry-runs, incident evidence and
            alerts carry aggregates and metrics only. Takes effect on the next run, no restart.
          </Typography.Text>
          {current.env_forced && (
            <Alert
              type="info"
              showIcon
              title="Forced on by PRIVACY_ZERO_SAMPLE_MODE"
              description="This deployment pins the mode on. It can be turned off only by clearing that variable and redeploying."
            />
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Turning it on does not delete samples already stored; the retention sweep does that on
            its schedule (see <Link to="/admin/compliance">Compliance</Link>).
            {current.updated_at &&
              ` Last changed ${formatTimestamp(current.updated_at)}${current.updated_by ? ` by ${current.updated_by}` : ''}.`}
          </Typography.Text>
        </Flex>
      )}
    </Card>
  );
}
