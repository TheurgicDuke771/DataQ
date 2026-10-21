import { Alert, App, Button, Checkbox, Flex, Form, Input, List, Tag, Typography } from 'antd';
import { useState } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Line,
  ReferenceArea,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  type DetectedPeriod,
  type DetectedSeasonalPeriods,
  promoteDetectedPeriod,
  type SeasonalPeriod,
} from '../../api/seasonalPeriods';
import type { SeasonalPoint } from '../../api/suites';
import { useIsWorkspaceAdmin } from '../../auth/useMe';
import { useFocusFallback } from '../../hooks/useFocusFallback';
import { errorMessage } from '../../utils/errors';
import { apiFieldError } from '../../utils/fieldErrors';
import { SEVERITY_SCALE } from '../../theme';
import { AXIS_TICK, CHART_COLORS, GRID_PROPS, TOOLTIP_STYLE } from '../charts/chartTheme';
import { ResponsiveChart } from '../charts/ResponsiveChart';
import { Modal } from '../shared/Modal';
import { DAY_MS, dayMs, declaredAreasBetween, spanMs } from './seasonalTrendData';

/** One plotted period of a per-period anomaly check. */
export interface PeriodRow {
  day: string;
  value: number;
  backfilled: boolean;
  /** What the run that judged this period was scored against, if one did. */
  judged: SeasonalPoint | null;
}

const DECLARED_FILL = SEVERITY_SCALE.warning;

/** What a detection that looked at nothing says instead of "none found". */
const DETECTION_REASON: Record<string, string> = {
  no_history: 'Detected periods: not looked for yet — the check has no stored periods.',
  insufficient_history: 'Detected periods: not enough history yet to look for recurring peaks.',
  invalid_config: 'Detected periods: not looked for — the check’s settings could not be read.',
  not_a_per_period_metric: 'Detected periods: only a per-period metric is looked at.',
};
const DETECTED_STROKE = CHART_COLORS.axis;

function SourceDot(props: unknown) {
  const { cx, cy, payload, index } = props as {
    cx?: number;
    cy?: number;
    index: number;
    payload: { backfilled: boolean };
  };
  if (cx === undefined || cy === undefined) return <g key={index} />;
  // Seeded points are hollow, observed ones filled: readable without color.
  return payload.backfilled ? (
    <circle
      key={index}
      cx={cx}
      cy={cy}
      r={2.5}
      fill="none"
      stroke={CHART_COLORS.axis}
      strokeWidth={1}
    />
  ) : (
    <circle key={index} cx={cx} cy={cy} r={2.5} fill={CHART_COLORS.primary} />
  );
}

/**
 * The stored periods with, for each period a run judged, the band it was scored against and,
 * where the yearly comparison applied, the expected value; attached calendar entries shaded and
 * detected periods outlined, each over the dates it covers.
 */
export function PeriodBaselineChart({
  rows,
  k,
  declared,
  detected,
  formatDay,
}: {
  rows: PeriodRow[];
  k: number;
  declared: SeasonalPeriod[];
  detected: DetectedPeriod[];
  formatDay: (day: string) => string;
}) {
  const data = rows.map((r) => {
    const j = r.judged;
    const mean = j?.banded ? j.mean : null;
    const stddev = j?.banded ? j.stddev : null;
    const banded = mean !== null && stddev !== null;
    return {
      x: dayMs(r.day),
      value: r.value,
      backfilled: r.backfilled,
      expected: j?.expected ?? null,
      band: banded ? [mean - k * stddev, mean + k * stddev] : null,
      // The centre with a whisker of k spreads: visible even for a period with no judged
      // neighbour, where the shaded band has nothing to join.
      centre: banded ? mean : null,
      spread: banded ? k * stddev : null,
    };
  });
  const first = rows[0]?.day;
  const last = rows[rows.length - 1]?.day;
  const declaredAreas = first ? declaredAreasBetween(declared, first, last) : [];
  const detectedAreas = detected.flatMap((d) =>
    d.occurrences.map((o) => ({ key: `${d.start}${o.start}`, ...spanMs(o) })),
  );
  const domain: [number, number] = first ? [dayMs(first), dayMs(last) + DAY_MS] : [0, DAY_MS];
  const tick = (ms: number) => formatDay(new Date(ms).toISOString().slice(0, 10));

  return (
    <ResponsiveChart
      height={180}
      isEmpty={data.length === 0}
      emptyText="No baseline observations recorded yet"
    >
      <ComposedChart
        data={data}
        margin={{ top: 14, right: 12, left: -16, bottom: 0 }}
        title="Stored periods and the band each judged period was scored against"
        desc="The caption above describes the band, the expected line and the shaded periods; the Table view lists every value."
      >
        <CartesianGrid {...GRID_PROPS} vertical={false} />
        <XAxis
          dataKey="x"
          type="number"
          scale="time"
          domain={domain}
          tick={AXIS_TICK}
          tickLine={false}
          minTickGap={24}
          tickFormatter={tick}
        />
        <YAxis tick={AXIS_TICK} tickLine={false} width={40} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ms) => tick(Number(ms))} />
        {declaredAreas.map((a) => (
          <ReferenceArea
            key={a.key}
            x1={a.x1}
            x2={a.x2}
            ifOverflow="hidden"
            fill={DECLARED_FILL}
            fillOpacity={0.12}
            label={{ value: a.name, position: 'insideTop', fontSize: 10 }}
          />
        ))}
        {detectedAreas.map((a) => (
          <ReferenceArea
            key={a.key}
            x1={a.x1}
            x2={a.x2}
            ifOverflow="hidden"
            fill="none"
            stroke={DETECTED_STROKE}
            strokeDasharray="4 3"
          />
        ))}
        <Area
          dataKey="band"
          name="Scored against"
          stroke="none"
          fill={CHART_COLORS.primary}
          fillOpacity={0.12}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Scatter
          dataKey="centre"
          name="Scored-against centre"
          fill="none"
          isAnimationActive={false}
        >
          <ErrorBar dataKey="spread" direction="y" stroke={CHART_COLORS.primary} width={4} />
        </Scatter>
        <Line
          dataKey="expected"
          name="Expected"
          stroke={CHART_COLORS.axis}
          strokeDasharray="6 3"
          strokeWidth={1.5}
          dot={{ r: 2, fill: CHART_COLORS.axis }}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="value"
          name="Value"
          stroke={CHART_COLORS.primary}
          strokeWidth={1.5}
          dot={SourceDot}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveChart>
  );
}

function spanText(p: { start: string; end: string }): string {
  return p.start === p.end ? p.start : `${p.start} – ${p.end}`;
}

/** The periods the check's history shows as peaks, each with a promote action for admins. */
export function DetectedPeriodsList({
  detected,
  suiteId,
  checkId,
  yearly,
  onPromoted,
}: {
  detected: DetectedSeasonalPeriods;
  suiteId: string;
  checkId: string;
  yearly: boolean;
  onPromoted: (entryId: string, attached: boolean) => void;
}) {
  const isAdmin = useIsWorkspaceAdmin();
  const [promoting, setPromoting] = useState<DetectedPeriod | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const { ref: fallbackRef, restore: restoreFocus } = useFocusFallback();
  const ratio = detected.thresholds.ratio;

  if (detected.periods.length === 0) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="detected-none">
        {DETECTION_REASON[detected.reason ?? ''] ??
          `Detected periods: none — no run of periods at ${ratio}× the level or more in ${detected.history.points} stored points.`}
      </Typography.Text>
    );
  }

  return (
    <Flex vertical gap={4} data-testid="detected-periods" ref={fallbackRef} tabIndex={-1}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Detected periods (outlined on the chart): runs of periods at {ratio}× the level or more in
        this check’s own history. They change no verdict unless an admin adds one to the calendar
        and it is attached to the check.
      </Typography.Text>
      <List
        size="small"
        dataSource={detected.periods}
        renderItem={(p) => (
          <List.Item
            actions={
              isAdmin && p.declared.length === 0
                ? [
                    <Button
                      key="promote"
                      size="small"
                      aria-label={`Promote to calendar: ${spanText(p)}`}
                      onClick={() => {
                        setPromoting(p);
                        setPromoteOpen(true);
                      }}
                    >
                      Promote to calendar
                    </Button>,
                  ]
                : undefined
            }
          >
            <Flex vertical gap={2}>
              <Flex gap={8} align="center" wrap>
                <Tag>
                  {p.kind === 'recurring' ? `Recurring, ${p.years_seen} years` : 'Seen once'}
                </Tag>
                <Typography.Text>{spanText(p)}</Typography.Text>
                <Typography.Text type="secondary">peak {p.peak_ratio.toFixed(1)}×</Typography.Text>
              </Flex>
              {p.declared.length > 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Covered by {p.declared.map((d) => d.name).join(', ')} on the calendar.
                </Typography.Text>
              )}
            </Flex>
          </List.Item>
        )}
      />
      {promoting && (
        <PromoteModal
          period={promoting}
          open={promoteOpen}
          suiteId={suiteId}
          checkId={checkId}
          yearly={yearly}
          onClose={() => setPromoteOpen(false)}
          afterClose={() => {
            setPromoting(null);
            restoreFocus();
          }}
          onDone={(entryId, attached) => {
            setPromoteOpen(false);
            onPromoted(entryId, attached);
          }}
        />
      )}
    </Flex>
  );
}

interface PromoteValues {
  name: string;
  series?: string;
  note?: string;
  attach: boolean;
}

function PromoteModal({
  period,
  open,
  afterClose,
  suiteId,
  checkId,
  yearly,
  onClose,
  onDone,
}: {
  period: DetectedPeriod;
  open: boolean;
  afterClose: () => void;
  suiteId: string;
  checkId: string;
  yearly: boolean;
  onClose: () => void;
  onDone: (entryId: string, attached: boolean) => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<PromoteValues>();
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const recurring = period.kind === 'recurring';

  const onFinish = async (values: PromoteValues) => {
    setSaving(true);
    try {
      const text = (v?: string) => (v?.trim() ? v.trim() : null);
      setRefusal(null);
      const created = await promoteDetectedPeriod({
        suite_id: suiteId,
        check_id: checkId,
        start: period.start,
        name: values.name.trim(),
        series: recurring ? null : text(values.series),
        note: text(values.note),
        attach: yearly && values.attach,
      });
      message.success(
        yearly && values.attach
          ? `${values.name.trim()} added to the calendar and attached to this check`
          : `${values.name.trim()} added to the calendar`,
      );
      onDone(created.id, yearly && values.attach);
    } catch (err) {
      const api = apiFieldError(err);
      if (api?.code === 'seasonal_period_name_taken') {
        form.setFields([{ name: 'name', errors: [api.message] }]);
      } else if (api) {
        // e.g. an entry already covers the season, or the history no longer shows it.
        setRefusal(api.message);
      } else {
        message.error(`Promote failed: ${errorMessage(err)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="Add this period to the calendar"
      onCancel={onClose}
      afterClose={afterClose}
      destroyOnHidden
      okText="Add to calendar"
      okButtonProps={{ loading: saving }}
      onOk={() => form.submit()}
    >
      {refusal && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          data-testid="promote-refusal"
          title={refusal}
        />
      )}
      <Typography.Paragraph type="secondary">
        {recurring && period.range
          ? 'It recurs at the same time each year, so it becomes an entry for every year over the span it covered.'
          : `It was seen once (${spanText(period)}), so it becomes a dated entry. Give it a series to match it with next year’s.`}
      </Typography.Paragraph>
      <Form<PromoteValues>
        name="promote-period"
        form={form}
        layout="vertical"
        initialValues={{ name: '', attach: yearly }}
        onFinish={(v) => void onFinish(v)}
      >
        <Form.Item
          name="name"
          label="Name"
          rules={[{ required: true, whitespace: true, message: 'Required' }]}
        >
          <Input maxLength={128} placeholder="e.g. Black Friday" />
        </Form.Item>
        {!recurring && (
          <Form.Item name="series" label="Series (optional)">
            <Input maxLength={128} />
          </Form.Item>
        )}
        <Form.Item name="note" label="Note (optional)">
          <Input.TextArea maxLength={2000} rows={2} />
        </Form.Item>
        <Form.Item
          name="attach"
          valuePropName="checked"
          extra={
            yearly
              ? 'An entry changes no check until it is attached. Attaching changes how this check scores the period from its next run.'
              : 'Attaching needs yearly seasonality on this check, so the entry is only added to the calendar.'
          }
        >
          <Checkbox disabled={!yearly}>Also attach it to this check</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
}
