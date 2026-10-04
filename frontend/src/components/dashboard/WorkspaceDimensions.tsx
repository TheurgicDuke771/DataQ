import { Spin, Typography } from 'antd';

import { getWorkspaceDimensions } from '../../api/dashboard';
import { useAsyncData } from '../../hooks/useAsyncData';
import { ScorecardPanel } from '../assets/ScorecardPanel';

/**
 * One DQ dimension across every asset (`/dashboard`) — the cut the per-asset scorecards hide
 * from each other. Workspace-wide like the asset-health lead above it, unlike the tiles below.
 */
export function WorkspaceDimensions() {
  const { state } = useAsyncData(() => getWorkspaceDimensions());

  if (state.status === 'loading') return <Spin />;
  if (state.status === 'error') {
    return (
      <Typography.Text type="secondary">
        The workspace dimension rollup is unavailable right now.
      </Typography.Text>
    );
  }
  return (
    <ScorecardPanel
      scorecard={state.data}
      title="Data quality by dimension, across the workspace"
      subject="the workspace"
      note="Every suite in the workspace, including ones you cannot open. Each suite counts through its latest run, and only once that run has completed."
    />
  );
}
