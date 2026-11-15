import { Segmented } from 'antd';

export type ChartView = 'chart' | 'table';

/** The Chart / Table switch beside a chart (#1673): a chart is pictures to a screen reader, so its
 *  numbers are a click away as a table. ``label`` names the switch for assistive technology
 *  (e.g. "Quality Trends view"), so it is tied to the chart it controls. */
export function ChartTableSwitch({
  value,
  onChange,
  label,
}: {
  value: ChartView;
  onChange: (view: ChartView) => void;
  label: string;
}) {
  return (
    <Segmented
      size="small"
      aria-label={label}
      value={value}
      onChange={(v) => onChange(v as ChartView)}
      options={[
        { label: 'Chart', value: 'chart' },
        { label: 'Table', value: 'table' },
      ]}
      style={{ marginBottom: 8 }}
    />
  );
}
