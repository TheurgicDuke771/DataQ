import { Alert, Button, Flex, Typography } from 'antd';

import { CONNECTION_KIND, type ConnectionType } from '../../api/connections';

/** When saving untested is the right call for this type (#1927) — never a reason to skip a real
 *  credential or config mistake. */
function untestedSaveHint(type: ConnectionType): string {
  if (type === 'dbt') {
    return (
      'A new dbt project has no run_results.json until its first build publishes one, so its ' +
      'test fails even when the path and credential are right. If that is why, save without ' +
      'testing, run the job once, then test again — the poller picks the run up.'
    );
  }
  if (CONNECTION_KIND[type] === 'orchestration') {
    return (
      'If the orchestrator is down or not reachable from DataQ right now, you can save ' +
      'without testing and test again once it is back.'
    );
  }
  return (
    'If this store is not reachable from the DataQ API right now (for example, only the ' +
    'workers can reach it), you can save without testing.'
  );
}

/**
 * The save-time test failure (#1927), shown on the form: the server wrote nothing. Offers the
 * Admin-only escape hatch, labelled as such — the audit log records every untested save.
 */
export function SaveTestFailedAlert({
  type,
  reason,
  skipLabel,
  skipping,
  onSkip,
}: {
  type: ConnectionType;
  reason: string;
  skipLabel: string;
  skipping: boolean;
  onSkip: () => void;
}) {
  return (
    <Alert
      type="error"
      showIcon
      style={{ marginBottom: 16 }}
      title="Connection test failed, so nothing was saved"
      description={
        <Flex vertical gap={8} align="start">
          <Typography.Text>{reason}</Typography.Text>
          <Typography.Text type="secondary">{untestedSaveHint(type)}</Typography.Text>
          <Button danger loading={skipping} onClick={onSkip}>
            {skipLabel}
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Admins only. The saved connection is not verified, and the audit log records that its
            test was skipped.
          </Typography.Text>
        </Flex>
      }
    />
  );
}
