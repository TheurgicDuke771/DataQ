import { Alert, Empty, Segmented, Spin, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  type CheckBaseline,
  type CheckResultPoint,
  getCheckBaseline,
  listCheckHistory,
} from '../../api/suites';
import { useAsyncData } from '../../hooks/useAsyncData';
import {
  AXIS_TICK,
  CHART_COLORS,
  GRID_PROPS,
  severityColor,
  TOOLTIP_STYLE,
} from '../charts/chartTheme';
import type { ResultStatus } from '../../api/runs';
import { formatTimestamp } from '../results/resultsFormat';
import { ResponsiveChart } from '../charts/ResponsiveChart';

/**
 * Per-check historical trend (#594, upgrading the Phase 2.6/ADR 0022 minimal
 * chart): a check's `metric_value` over its recent runs, banded by its own
 * warn/fail/critical thresholds, plus — for an `anomaly` check with a captured
 * baseline (#593) — a second panel that overlays the learned mean±kσ band on
 * the raw measurements behind the score. This is deliberately TWO panels, not
 * one dual-axis chart: `metric_value` for an anomaly check is the z-SCORE
 * (banded against the same thresholds every other kind uses — ADR 0016), while
 * the baseline's `observations` are the raw measurement (row count / freshness
 * hours) it was scored against. Those are different units on different scales;
 * forcing them onto one axis would misrepresent both. Showing them as
 * synchronized panels in one component still satisfies "one component serves
 * both stories" and "doubles as the model's visual debugger" (issue #594).
 *
 * A11y (#594, carrying forward the Theme 3 lesson): severity is never color-only
 * — each threshold line also carries a distinct dash pattern and a text label —
 * and a "Table" view renders the identical data as a plain `<Table>` for anyone
 * who can't read the chart at all.
 *
 * Lazily fetched per check (only when a run-detail row expands, or the check
 * editor's Trend drawer is opened), so it doesn't fan out a request per check on
 * page load. recharts stays out of the initial bundle because every caller of
 * this component lives behind a lazy route (`RunDetail`, `CheckEdit` — ADR 0022).
 */
interface CheckTrendCheck {
  id: string;
  kind: string;
  warn_threshold: number | null;
  fail_threshold: number | null;
  critical_threshold: number | null;
}

interface CheckTrendProps {
  suiteId: string;
  check: CheckTrendCheck;
  /** History window (backend caps at 180); 90 gives a debugger-useful span
   *  without ever hitting the cap. */
  limit?: number;
}

/** ISO timestamp → short `Jun 13` label. */
function shortDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
}

/** Severity → a dash pattern distinct enough to read without color (a11y). */
const THRESHOLD_DASH: Record<'warn' | 'fail' | 'critical', string> = {
  warn: '4 3',
  fail: '9 4',
  critical: '2 2',
};

interface ThresholdBand {
  tier: 'warn' | 'fail' | 'critical';
  value: number;
}

function thresholdBands(check: CheckTrendCheck): ThresholdBand[] {
  const bands: ThresholdBand[] = [];
  if (check.warn_threshold !== null) bands.push({ tier: 'warn', value: check.warn_threshold });
  if (check.fail_threshold !== null) bands.push({ tier: 'fail', value: check.fail_threshold });
  if (check.critical_threshold !== null) {
    bands.push({ tier: 'critical', value: check.critical_threshold });
  }
  return bands;
}

/** "Warn ≥ 1" — shared by the chart's `ReferenceLine` label and the plain-text
 *  caption below, so the two never drift. */
function bandLabel(band: ThresholdBand): string {
  return `${band.tier[0].toUpperCase()}${band.tier.slice(1)} ≥ ${band.value}`;
}

/** One raw baseline observation, loosely parsed from the untyped JSONB payload. */
interface Observation {
  ts: string;
  value: number;
}

/** Pulls `{ts, value}` observations out of an `anomaly` baseline's payload
 *  (`backend/app/services/anomaly.py`'s documented shape). Defensive against a
 *  malformed/foreign-kind payload — this layer doesn't validate it server-side
 *  either (`check_service.get_check_baseline` returns it generically), so an
 *  odd entry is dropped rather than crashing the debugger panel. */
function parseObservations(baseline: CheckBaseline | null): Observation[] {
  if (!baseline || baseline.kind !== 'anomaly') return [];
  const raw = baseline.baseline.observations;
  if (!Array.isArray(raw)) return [];
  const out: Observation[] = [];
  for (const entry of raw) {
    if (entry === null || typeof entry !== 'object') continue;
    const { ts, value } = entry as Record<string, unknown>;
    if (typeof ts === 'string' && typeof value === 'number' && Number.isFinite(value)) {
      out.push({ ts, value });
    }
  }
  return out;
}

/** Sample mean/stddev (n-1) — mirrors `anomaly.py`'s `score()`, which needs at
 *  least 2 points for a defined stddev. */
function meanStddev(values: number[]): { mean: number; stddev: number } | null {
  if (values.length < 2) return null;
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (values.length - 1);
  return { mean, stddev: Math.sqrt(variance) };
}

export function CheckTrend({ suiteId, check, limit = 90 }: CheckTrendProps) {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const isAnomaly = check.kind === 'anomaly';

  const { state } = useAsyncData(async () => {
    const [history, baseline] = await Promise.all([
      listCheckHistory(suiteId, check.id, limit),
      // Baseline is a debugging overlay, not the main story: a fetch failure
      // (or simply no baseline yet) degrades to "no overlay", never an error
      // for the whole trend.
      isAnomaly ? getCheckBaseline(suiteId, check.id).catch(() => null) : Promise.resolve(null),
    ]);
    return { history, baseline };
  });

  if (state.status === 'loading') return <Spin size="small" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load trend" description={state.error} />;
  }

  const { history, baseline } = state.data;
  const withMetric = history.filter((p) => p.metric_value !== null);
  const bands = thresholdBands(check);
  const observations = parseObservations(baseline);

  return (
    <div>
      <Segmented
        size="small"
        value={view}
        onChange={(v) => setView(v as 'chart' | 'table')}
        options={[
          { label: 'Chart', value: 'chart' },
          { label: 'Table', value: 'table' },
        ]}
        style={{ marginBottom: 8 }}
      />
      {/* Plain-text mirror of the chart's threshold lines (a11y, #594): the
          dash-pattern + colour encoding on the chart itself is non-color-only
          already, but a screen reader gets nothing from an SVG reference line —
          this is the equivalent information as real, readable DOM text. */}
      {bands.length > 0 && (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, display: 'block', marginBottom: 4 }}
        >
          Thresholds: {bands.map(bandLabel).join(' · ')}
        </Typography.Text>
      )}
      {view === 'chart' ? (
        <>
          <MetricChart points={withMetric} bands={bands} />
          {isAnomaly && <AnomalyBaselinePanel observations={observations} check={check} />}
        </>
      ) : (
        <TrendTable points={history} observations={isAnomaly ? observations : null} />
      )}
    </div>
  );
}

// ───────────────────────── primary metric chart ─────────────────────

function MetricChart({ points, bands }: { points: CheckResultPoint[]; bands: ThresholdBand[] }) {
  const data = points.map((p) => ({
    label: shortDay(p.created_at),
    metric: p.metric_value,
    status: p.status as ResultStatus,
  }));

  return (
    <ResponsiveChart height={180} isEmpty={data.length === 0} emptyText="No metric history yet">
      <LineChart data={data} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS_TICK} tickLine={false} width={40} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        {bands.map((band) => (
          <ReferenceLine
            key={band.tier}
            y={band.value}
            stroke={severityColor(band.tier)}
            strokeDasharray={THRESHOLD_DASH[band.tier]}
            strokeWidth={1.5}
            ifOverflow="extendDomain"
            label={{
              value: bandLabel(band),
              position: 'insideTopRight',
              fill: severityColor(band.tier),
              fontSize: 10,
            }}
          />
        ))}
        <Line
          type="monotone"
          dataKey="metric"
          name="Metric"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={(props) => {
            const { cx, cy, payload, index } = props as {
              cx: number;
              cy: number;
              index: number;
              payload: { status: ResultStatus };
            };
            return (
              <circle
                key={index}
                cx={cx}
                cy={cy}
                r={3.5}
                fill={severityColor(payload.status)}
                stroke="#fff"
                strokeWidth={1}
              />
            );
          }}
        />
      </LineChart>
    </ResponsiveChart>
  );
}

// ───────────────────────── anomaly baseline overlay (#593 debugger) ─

/** Fallback z-multiplier when the check carries no `fail_threshold` — mean±2σ
 *  covers ~95% of a normal distribution, a reasonable "this would look
 *  anomalous" default. When a threshold IS set, using it as `k` makes the
 *  shaded band literally the boundary at which a future measurement would
 *  score `fail` — the debugger view then matches the check's real behaviour
 *  instead of an arbitrary reference width. */
const DEFAULT_BAND_K = 2;

function AnomalyBaselinePanel({
  observations,
  check,
}: {
  observations: Observation[];
  check: CheckTrendCheck;
}) {
  const stats = useMemo(() => meanStddev(observations.map((o) => o.value)), [observations]);
  const k =
    check.fail_threshold !== null && check.fail_threshold > 0
      ? check.fail_threshold
      : DEFAULT_BAND_K;

  const data = observations.map((o) => ({ label: shortDay(o.ts), value: o.value }));

  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Anomaly baseline — learned band (mean ± {k}σ)
        {stats && (
          <>
            {' · '}μ={stats.mean.toFixed(2)} · σ={stats.stddev.toFixed(2)} · {observations.length}{' '}
            points
          </>
        )}
      </Typography.Text>
      <ResponsiveChart
        height={140}
        isEmpty={data.length === 0}
        emptyText="No baseline observations recorded yet"
      >
        <LineChart data={data} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} vertical={false} />
          <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS_TICK} tickLine={false} width={40} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          {stats && (
            <ReferenceArea
              y1={stats.mean - k * stats.stddev}
              y2={stats.mean + k * stats.stddev}
              fill={CHART_COLORS.primary}
              fillOpacity={0.08}
              ifOverflow="extendDomain"
            />
          )}
          {stats && (
            <ReferenceLine
              y={stats.mean}
              stroke={CHART_COLORS.axis}
              strokeDasharray="3 3"
              label={{ value: 'mean', position: 'insideTopRight', fontSize: 10 }}
            />
          )}
          <Line
            type="monotone"
            dataKey="value"
            name="Observation"
            stroke={CHART_COLORS.primary}
            strokeWidth={1.5}
            dot={{ r: 2.5 }}
          />
        </LineChart>
      </ResponsiveChart>
    </div>
  );
}

// ───────────────────────── a11y data-table fallback ─────────────────

function TrendTable({
  points,
  observations,
}: {
  points: CheckResultPoint[];
  observations: Observation[] | null;
}) {
  if (points.length === 0 && (!observations || observations.length === 0)) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No metric history yet" />;
  }
  return (
    <>
      <Table<CheckResultPoint>
        size="small"
        rowKey="run_id"
        pagination={false}
        dataSource={[...points].reverse()} // newest first, like the results table
        columns={[
          {
            title: 'Run time',
            dataIndex: 'created_at',
            render: (v: string) => formatTimestamp(v),
          },
          {
            title: 'Metric',
            dataIndex: 'metric_value',
            render: (v: number | null) => (v === null ? '—' : v),
          },
          {
            title: 'Status',
            dataIndex: 'status',
            render: (s: ResultStatus) => <Tag color={severityColor(s)}>{s}</Tag>,
          },
        ]}
      />
      {observations && observations.length > 0 && (
        <>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, marginTop: 12, display: 'block' }}
          >
            Anomaly baseline observations (raw measurements)
          </Typography.Text>
          <Table<Observation>
            size="small"
            rowKey={(o) => o.ts}
            pagination={false}
            dataSource={[...observations].reverse()}
            columns={[
              { title: 'Observed at', dataIndex: 'ts', render: (v: string) => formatTimestamp(v) },
              { title: 'Value', dataIndex: 'value' },
            ]}
          />
        </>
      )}
    </>
  );
}
