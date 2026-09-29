import { App, Flex, Switch, Typography } from 'antd';
import { useState } from 'react';

import { updateAsset } from '../../api/assets';
import { errorMessage } from '../../utils/errors';

/**
 * Workspace-admin switch that leaves one table out of its connection's automatic coverage
 * (ADR 0047). Excluding pauses the table's automatic suite; it deletes nothing.
 */
export function CoverageBlock({
  assetId,
  excluded,
  onChanged,
}: {
  assetId: string;
  excluded: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [saving, setSaving] = useState(false);

  const onToggle = async (included: boolean) => {
    setSaving(true);
    try {
      await updateAsset(assetId, { auto_coverage_excluded: !included });
      onChanged();
    } catch (err) {
      message.error(errorMessage(err, 'Could not change automatic coverage.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Flex gap={8} align="center" wrap data-testid="coverage-block">
      <Switch
        checked={!excluded}
        loading={saving}
        onChange={onToggle}
        aria-label="Include this table in automatic coverage"
      />
      <Typography.Text>Include in automatic coverage</Typography.Text>
      <Typography.Text type="secondary">
        Applies when its connection has automatic coverage switched on. Excluding pauses this
        table’s automatic suite and keeps its history.
      </Typography.Text>
    </Flex>
  );
}
