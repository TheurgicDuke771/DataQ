import { Tag, Tooltip } from 'antd';

import { REDACTION_REASON_LABELS, type RedactionReason } from '../../api/runs';

/** Why a withheld `metric_value` was masked, as one sentence — also used by exports. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (enabled.tsx precedent)
export function metricMaskedText(redaction: RedactionReason | string): string {
  const reason = REDACTION_REASON_LABELS[redaction as RedactionReason] ?? redaction;
  return `Masked: this min/max is one cell of a sensitive column (${reason}).`;
}

/**
 * A result's `metric_value`, or a "Masked" badge when the API withheld it (`metric_value_redaction`
 * set): an `aggregate` min/max records one literal cell, so on a sensitive column the number is
 * never sent. Distinct from an em dash, which means the check records no metric.
 */
export function MetricValue({
  value,
  redaction,
  format = (v) => String(v),
}: {
  value: number | null | undefined;
  redaction?: RedactionReason | string | null;
  format?: (value: number) => string;
}) {
  if (redaction) {
    return (
      <Tooltip trigger={['hover', 'focus']} title={metricMaskedText(redaction)}>
        <Tag tabIndex={0} style={{ marginInlineEnd: 0 }}>
          Masked
        </Tag>
      </Tooltip>
    );
  }
  return <>{value === null || value === undefined ? '—' : format(value)}</>;
}
