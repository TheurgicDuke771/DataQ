import { Alert, App, Card, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type CoverageSettings,
  getCoverageSettings,
  putCoverageSettings,
  resetCoverageSettings,
} from '../../api/coverage';
import { CoverageSettingsForm } from '../../components/coverage-settings/CoverageSettingsForm';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { useFocusFallback } from '../../hooks/useFocusFallback';
import { fetchFailure } from '../../utils/errors';

/** Workspace sensitivity for automatic baselines (ADR 0047): applied by every automatic check
 *  that still inherits, on its next run. */
export function CoveragePanel() {
  const { state } = useAsyncData(() => getCoverageSettings());
  const [saved, setSaved] = useState<CoverageSettings | null>(null);
  const { run, loading } = useAsyncAction('Could not save the coverage settings');
  const current = saved ?? (state.status === 'ok' ? state.data : null);
  const { message } = App.useApp();
  const { ref: fallbackRef, restore: restoreFocus } = useFocusFallback();
  const apply = (request: () => Promise<CoverageSettings>, done: string) =>
    void run(async () => {
      setSaved(await request());
      message.success(done);
      restoreFocus();
    });

  return (
    <Card title="Automatic coverage" size="small">
      {state.status === 'loading' && !current && (
        <Typography.Text type="secondary">Loading…</Typography.Text>
      )}
      {state.status === 'error' && !current && (
        <Alert
          type="error"
          showIcon
          title="Could not load the coverage settings"
          description={fetchFailure(state.error).message}
        />
      )}
      {current && (
        <Flex vertical gap={12} ref={fallbackRef} tabIndex={-1}>
          <Flex align="center" gap={12} wrap>
            <Typography.Text strong>Sensitivity of automatic checks</Typography.Text>
            <Tag color={current.is_default ? 'default' : 'blue'}>
              {current.is_default ? 'Defaults' : 'Customised'}
            </Tag>
          </Flex>
          <Typography.Text type="secondary">
            How many standard deviations from a table’s own history make its automatic row-count,
            freshness and column-profile checks warn, fail and go critical, and how much history
            they compare against. A table can override any group on its asset page.
          </Typography.Text>
          <CoverageSettingsForm
            // Remount on save so the form starts again from what was stored.
            key={current.updated_at ?? 'default'}
            stored={current.setting}
            below={current.defaults}
            current={current.effective}
            scope="Set for the workspace"
            clearLabel="Reset to defaults"
            saving={loading}
            onSave={(doc) => apply(() => putCoverageSettings(doc), 'Coverage settings saved')}
            onClear={() => apply(resetCoverageSettings, 'Coverage settings reset to the defaults')}
          />
          <Alert
            type="info"
            showIcon
            title="Applies on each check’s next run"
            description="Automatic checks read these settings every time they run, so nothing needs editing. A check whose thresholds or window someone has edited keeps its own values and is not affected."
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
