import { Tag } from 'antd';

import type { AssetSummary } from '../../api/assets';
import { assetHealth } from './health';

/** Asset-level health badge (#760) — severity + run-state rolled into an antd Tag. */
export function AssetHealthTag({
  summary,
}: {
  summary: Pick<
    AssetSummary,
    'worst_severity' | 'last_run_at' | 'has_failed_run' | 'has_active_run'
  >;
}) {
  const { label, color } = assetHealth(summary);
  return <Tag color={color}>{label}</Tag>;
}
