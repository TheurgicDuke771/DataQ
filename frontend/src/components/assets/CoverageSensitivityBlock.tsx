import { Alert, App, Collapse, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';

import { updateAsset } from '../../api/assets';
import {
  type AssetCoverageSettings,
  getAssetCoverageSettings,
  getCoverageSettings,
} from '../../api/coverage';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { useFocusFallback } from '../../hooks/useFocusFallback';
import { fetchFailure } from '../../utils/errors';
import { CoverageSettingsForm } from '../coverage-settings/CoverageSettingsForm';

/**
 * Workspace-admin control for one table's own sensitivity (ADR 0047). What it sets wins over
 * the workspace setting for this table's automatic checks; what it leaves alone follows it.
 */
export function CoverageSensitivityBlock({ assetId }: { assetId: string }) {
  const { state, reload } = useAsyncData(async () => {
    const [asset, workspace] = await Promise.all([
      getAssetCoverageSettings(assetId),
      getCoverageSettings(),
    ]);
    return { asset, workspace };
  });
  const [saved, setSaved] = useState<AssetCoverageSettings | null>(null);
  const { run, loading } = useAsyncAction('Could not save this table’s sensitivity');
  const { message } = App.useApp();
  const { ref: fallbackRef, restore: restoreFocus } = useFocusFallback();

  if (state.status === 'loading') return null;
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Could not load this table’s coverage sensitivity"
        description={fetchFailure(state.error).message}
      />
    );
  }
  const asset = saved ?? state.data.asset;
  const overridden = asset.override !== null;
  // With no override the table follows the workspace, which may itself set nothing.
  const follows = [
    ...Object.values(asset.effective.sensitivity).map((group) => group.source),
    asset.effective.baseline.source,
  ].includes('workspace')
    ? 'Workspace setting'
    : 'Built-in default';
  const save = (config: AssetCoverageSettings['override']) =>
    void run(async () => {
      await updateAsset(assetId, { auto_coverage_config: config });
      message.success(
        config === null ? 'This table now follows the workspace setting' : 'Saved for this table',
      );
      try {
        setSaved(await getAssetCoverageSettings(assetId));
      } catch {
        // The override is stored; only the re-read failed. Load the block again rather than
        // report a failed save.
        setSaved(null);
        reload();
      }
      restoreFocus();
    });

  return (
    <Collapse
      size="small"
      data-testid="coverage-sensitivity"
      items={[
        {
          key: 'sensitivity',
          label: (
            <Flex gap={8} align="center" wrap>
              <Typography.Text>Sensitivity of this table’s automatic checks</Typography.Text>
              <Tag color={overridden ? 'blue' : 'default'}>
                {overridden ? 'Set for this table' : follows}
              </Tag>
            </Flex>
          ),
          children: (
            <div
              ref={fallbackRef}
              tabIndex={-1}
              role="group"
              aria-label="Sensitivity of this table’s automatic checks"
            >
              <CoverageSettingsForm
                key={JSON.stringify(asset.override)}
                stored={asset.override}
                below={state.data.workspace.effective}
                current={asset.effective}
                scope="Set for this table"
                clearLabel="Use the workspace setting"
                saving={loading}
                onSave={save}
                onClear={() => save(null)}
              />
            </div>
          ),
        },
      ]}
    />
  );
}
