import { Tag, Tooltip } from 'antd';

import type { Check } from '../../api/suites';

/** `enabled` is optional in the type (older fixtures); absent means switched on. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (snooze.tsx precedent)
export function isDisabled(check: Pick<Check, 'enabled'>): boolean {
  return check.enabled === false;
}

/** The "Disabled" badge for a check that is kept but left out of every run. */
export function DisabledTag({ check }: { check: Pick<Check, 'enabled'> }) {
  if (!isDisabled(check)) return null;
  return (
    <Tooltip title="Switched off: runs skip this check, so it records no result and raises no alert. Its history is kept.">
      <Tag style={{ marginInlineEnd: 0 }}>Disabled</Tag>
    </Tooltip>
  );
}
