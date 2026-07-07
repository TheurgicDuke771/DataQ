import { Tag, Tooltip } from 'antd';

import type { Check } from '../../api/suites';
import { formatTimestamp } from '../results/resultsFormat';

/** A check is snoozed only while the timestamp is in the future (#370).
 *  `now` is injected so list views can re-evaluate on a ticker (an expiry
 *  passing while the page is open must drop the badge). */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export function isSnoozed(check: Check, now: number = Date.now()): boolean {
  return check.alert_snoozed_until !== null && new Date(check.alert_snoozed_until).getTime() > now;
}

/**
 * The "Snoozed until …" badge, shared by every surface that lists checks
 * (suite detail, run detail). The copy is careful about the backend semantics
 * (#370): suppression is decided per RUN — an alert is muted only when every
 * failing check is snoozed, so one snoozed check doesn't silence a run alert
 * that other failures trigger.
 */
export function SnoozedTag({ check, now }: { check: Check; now?: number }) {
  if (!isSnoozed(check, now)) return null;
  return (
    <Tooltip title="This check won't trigger alerts by itself; a run alert still fires (and may list it) if other checks fail. Results keep recording.">
      <Tag color="orange" style={{ marginInlineEnd: 0 }}>
        Snoozed until {formatTimestamp(check.alert_snoozed_until)}
      </Tag>
    </Tooltip>
  );
}
