import { Descriptions } from 'antd';
import { useId } from 'react';

import { yearlyReasonLabel } from '../checks/seasonalTrendData';

type Obj = Record<string, unknown>;

const obj = (v: unknown): Obj | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : null;
const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

function fmt(v: number | null): string {
  return v === null ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(2);
}

/**
 * What a per-period anomaly result was judged on: the period, the yearly comparison (or why it did
 * not apply), the declared calendar entry the period fell inside, and whether the calendar changed
 * the verdict. Renders nothing for any other result.
 */
export function SeasonalResultSummary({ observed }: { observed: unknown }) {
  const titleId = useId();
  const v = obj(observed);
  const period = obj(v?.period);
  if (!v || !period) return null;
  const yearly = obj(v.yearly);
  const declared = obj(v.declared);
  const inside = obj(declared?.inside);
  const history = obj(v.history);
  const start = str(period.start);
  const end = str(period.end);

  const items = [
    {
      key: 'period',
      label: 'Period judged',
      children: start && end && start !== end ? `${start} – ${end}` : (start ?? '—'),
    },
  ];
  if (yearly) {
    const applied = yearly.applied === true;
    items.push({
      key: 'yearly',
      label: 'Yearly comparison',
      children: applied
        ? `expected ${fmt(num(yearly.expected))} (level ${fmt(num(yearly.level))} × index ${fmt(num(yearly.index))}, from ${fmt(num(yearly.years))} earlier year${num(yearly.years) === 1 ? '' : 's'})`
        : `not applied: ${yearlyReasonLabel(str(yearly.reason))}; scored against the recent window`,
    });
  }
  if (num(v.mean) !== null && num(v.stddev) !== null) {
    items.push({
      key: 'band',
      label: 'Scored against',
      children: `${fmt(num(v.mean))} ± ${fmt(num(v.stddev))} (z ${fmt(num(v.z_score))})`,
    });
  }
  if (declared) {
    const occurrence = obj(inside?.occurrence);
    items.push({
      key: 'declared',
      label: 'Declared period',
      children: inside
        ? `inside ${str(inside.name) ?? 'an attached entry'}${occurrence ? ` (${str(occurrence.start)} – ${str(occurrence.end)})` : ''}${inside.first_year === true ? '; its first occurrence in the history' : ''}${inside.banded === false ? ', so a rise was not banded' : ''}`
        : 'outside every attached entry',
    });
    const changed = declared.verdict_changed;
    items.push({
      key: 'verdict',
      label: 'Calendar effect',
      children:
        changed === true
          ? `changed the verdict: without the calendar it would have been ${str(declared.verdict_without) ?? 'unbanded'} (z ${fmt(num(declared.z_score_without))})`
          : 'did not change the verdict',
    });
    const missing = Array.isArray(declared.missing) ? declared.missing.length : 0;
    if (missing > 0) {
      items.push({
        key: 'missing',
        label: 'Missing entries',
        children: `${missing} attached entr${missing === 1 ? 'y is' : 'ies are'} no longer on the calendar`,
      });
    }
  }
  if (history) {
    items.push({
      key: 'history',
      label: 'History behind it',
      children: `${fmt(num(history.observed))} measured by runs, ${fmt(num(history.backfilled))} seeded from the table`,
    });
  }

  return (
    <div role="group" aria-labelledby={titleId}>
      <Descriptions
        size="small"
        column={1}
        bordered
        title={<span id={titleId}>How this period was judged</span>}
        items={items}
        data-testid="seasonal-result-summary"
      />
    </div>
  );
}
