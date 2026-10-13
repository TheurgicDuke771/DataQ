import { Alert, Button, Tag, Tooltip } from 'antd';

import type { Check } from '../../api/suites';
import { LEVEL_LABEL } from '../coverage-settings/coverageDraft';

const BASELINE_METRICS = new Set(['row_count', 'freshness_age_hours', 'column_profile']);

/** An automatic baseline that could follow the coverage settings but has its own values. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its components belong together
export function hasOwnValues(check: Check): boolean {
  return (
    check.origin === 'auto' &&
    check.kind === 'anomaly' &&
    !check.inherited &&
    BASELINE_METRICS.has(String(check.config.target_metric))
  );
}

/** Marks a check whose thresholds are the coverage settings in effect, not its own (ADR 0047). */
export function InheritedTag({ check }: { check: Check }) {
  if (!check.inherited) return null;
  return (
    <Tooltip
      title={`Thresholds from: ${LEVEL_LABEL[check.inherited.sensitivity]}. History from: ${LEVEL_LABEL[check.inherited.baseline]}. They follow the coverage settings until someone edits them on this check.`}
    >
      <Tag style={{ marginInlineEnd: 0 }}>Inherited thresholds</Tag>
    </Tooltip>
  );
}

/**
 * Says, in the check editor, where an automatic baseline's numbers come from and what editing
 * them does. A check with its own values gets the way back.
 */
export function InheritanceNotice({
  check,
  resuming,
  onResume,
}: {
  check: Check;
  resuming: boolean;
  onResume: () => void;
}) {
  if (check.inherited) {
    return (
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title="These thresholds come from the coverage settings"
        description={`Thresholds: ${LEVEL_LABEL[check.inherited.sensitivity].toLowerCase()}. History: ${LEVEL_LABEL[check.inherited.baseline].toLowerCase()}. Changing a threshold, the window, the minimum or seasonality here gives this check its own values, and later changes to the settings will no longer reach it.`}
      />
    );
  }
  if (!hasOwnValues(check)) return null;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 16 }}
      title="This automatic check has its own thresholds"
      description="Someone edited them, so changes to the coverage settings do not reach it."
      action={
        <Button size="small" loading={resuming} onClick={onResume}>
          Follow the coverage settings
        </Button>
      }
    />
  );
}
