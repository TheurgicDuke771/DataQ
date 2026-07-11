import { Tag } from 'antd';

import type { AssetSummary } from '../../api/assets';
import { assetHealth } from './health';

/** Asset-level health badge (#760) — the rolled-up worst-severity as an antd Tag. */
export function AssetHealthTag({
  summary,
}: {
  summary: Pick<AssetSummary, 'worst_severity' | 'last_run_at'>;
}) {
  const { label, color } = assetHealth(summary);
  return <Tag color={color}>{label}</Tag>;
}
