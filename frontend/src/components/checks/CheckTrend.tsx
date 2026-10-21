import { Alert, Empty, Flex, Segmented, Spin, Table, Typography } from 'antd';
import { useState } from 'react';
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
  type SeasonalPoint,
} from '../../api/suites';
import {
  type DetectedSeasonalPeriods,
  getDetectedSeasonalPeriods,
  listSeasonalPeriods,
  type SeasonalPeriod,
} from '../../api/seasonalPeriods';
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
import { StatusMarker, StatusMarkerKey } from '../charts/StatusMarker';
import { ResultStatusTag } from '../shared/StatusTag';
import { DetectedPeriodsList, PeriodBaselineChart, type PeriodRow } from './SeasonalTrend';
import {
  declaredAreasBetween,
  isoDay,
  resultsByPeriod,
  yearlyReasonLabel,
  yearlySummary,
} from './seasonalTrendData';

/**
 * Per-check historical trend (#594, upgrading the Phase 2.6/ADR 0022 minimal chart): a check's
 * `metric_value` over its recent runs, banded by its own warn/fail/critical thresholds, plus.
 */
interface CheckTrendCheck {
  id: string;
  kind: string;
  warn_threshold: number | null;
  fail_threshold: number | null;
  critical_threshold: number | null;
  /** An `aggregate` check's two-sided bands live here, not in the threshold columns. */
  config?: Record<string, unknown>;
}

interface CheckTrendProps {
  suiteId: string;
  check: CheckTrendCheck;
  /** History window (backend caps at 180); 90 gives a debugger-useful span
   *  without ever hitting the cap. A per-period check reads the cap, 180; its caption says when that leaves older periods without a band. */
  limit?: number;
  /** Called with the new entry's id after a detected period was promoted and attached to this
   *  check: an open editor must add it to its own copy of the config, or a save drops it. */
  onAttached?: (entryId: string) => void;
}

/** A per-period anomaly check (`period_row_count` / `period_sum`) names its date column. */
function isPerPeriod(check: CheckTrendCheck): boolean {
  return check.kind === 'anomaly' && typeof check.config?.date_column === 'string';
}

function attachedIds(check: CheckTrendCheck): string[] {
  const raw = check.config?.seasonal_periods;
  return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === 'string') : [];
}

/** ISO timestamp → short `Jun 13` label. */
function shortDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
}

/** `Jun 13, 25`: for a series long enough that a day and month alone repeat. */
function dayWithYear(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: '2-digit',
    year: '2-digit',
    timeZone: 'UTC',
  });
}

/** A seeded per-period baseline holds about three years of points; the chart and the table
 *  show the most recent of them and say how many there are. */
const BASELINE_POINTS_SHOWN = 180;
const YEAR_MS = 365 * 24 * 60 * 60 * 1000;

/** Severity → a dash pattern distinct enough to read without color (a11y). */
const THRESHOLD_DASH: Record<'warn' | 'fail' | 'critical', string> = {
  warn: '4 3',
  fail: '9 4',
  critical: '2 2',
};

interface ThresholdBand {
  tier: 'warn' | 'fail' | 'critical';
  value: number;
  /** `≥` for the one-sided threshold columns; `<`/`>` for an aggregate's two-sided bounds. */
  op: '≥' | '<' | '>';
}

/** An `aggregate` check's config bounds (backend `monitors.AGGREGATE_*_BOUNDS`), as bands. */
const AGGREGATE_BOUNDS: { key: string; tier: ThresholdBand['tier']; op: '<' | '>' }[] = [
  { key: 'critical_min', tier: 'critical', op: '<' },
  { key: 'min_value', tier: 'fail', op: '<' },
  { key: 'warn_min', tier: 'warn', op: '<' },
  { key: 'warn_max', tier: 'warn', op: '>' },
  { key: 'max_value', tier: 'fail', op: '>' },
  { key: 'critical_max', tier: 'critical', op: '>' },
];

function thresholdBands(check: CheckTrendCheck): ThresholdBand[] {
  if (check.kind === 'aggregate') {
    const config = check.config ?? {};
    return AGGREGATE_BOUNDS.flatMap(({ key, tier, op }) => {
      const value = config[key];
      return typeof value === 'number' ? [{ tier, value, op }] : [];
    });
  }
  const bands: ThresholdBand[] = [];
  if (check.warn_threshold !== null) {
    bands.push({ tier: 'warn', value: check.warn_threshold, op: '≥' });
  }
  if (check.fail_threshold !== null) {
    bands.push({ tier: 'fail', value: check.fail_threshold, op: '≥' });
  }
  if (check.critical_threshold !== null) {
    bands.push({ tier: 'critical', value: check.critical_threshold, op: '≥' });
  }
  return bands;
}

/** "Warn ≥ 1" — shared by the chart's `ReferenceLine` label and the plain-text
 *  caption below, so the two never drift. */
function bandLabel(band: ThresholdBand): string {
  return `${band.tier[0].toUpperCase()}${band.tier.slice(1)} ${band.op} ${band.value}`;
}

/** One raw baseline observation, loosely parsed from the untyped JSONB payload. */
interface Observation {
  ts: string;
  value: number;
  /** Seeded from the table's own rows, not measured by a run (a per-period check's first run). */
  backfilled: boolean;
}

/**
 * Pulls `{ts, value}` observations out of an `anomaly` baseline's payload
 * (`backend/app/services/anomaly.py`'s documented shape).
 */
/** A `column_profile` baseline keeps one history per column, not a single series. */
function isColumnProfile(baseline: CheckBaseline | null): baseline is CheckBaseline {
  return baseline?.kind === 'anomaly' && baseline.baseline.target_metric === 'column_profile';
}

function columnProfileRuns(baseline: CheckBaseline): number {
  const raw = baseline.baseline.observations;
  return Array.isArray(raw) ? raw.length : 0;
}

function parseObservations(baseline: CheckBaseline | null): Observation[] {
  if (!baseline || baseline.kind !== 'anomaly') return [];
  const raw = baseline.baseline.observations;
  if (!Array.isArray(raw)) return [];
  const out: Observation[] = [];
  for (const entry of raw) {
    if (entry === null || typeof entry !== 'object') continue;
    const { ts, value, source } = entry as Record<string, unknown>;
    if (typeof ts === 'string' && typeof value === 'number' && Number.isFinite(value)) {
      out.push({ ts, value, backfilled: source === 'backfilled' });
    }
  }
  return out;
}

/**
 * Sample mean/stddev (n-1) — mirrors `anomaly.py`'s `score()`, which needs at least 2 points for a
 * defined stddev.
 */
function meanStddev(values: number[]): { mean: number; stddev: number } | null {
  if (values.length < 2) return null;
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (values.length - 1);
  return { mean, stddev: Math.sqrt(variance) };
}

/**
 * The `window`/`seasonality` a baseline was captured with — `dump_baseline` (anomaly.py) always
 * writes both, so this is a straight read; the fallback (`_ANOMALY_DEFAULT_WINDOW` = 14.
 */
interface AnomalyBaselineMeta {
  window: number;
  seasonality: boolean;
}

const DEFAULT_ANOMALY_WINDOW = 14;

function parseAnomalyMeta(baseline: CheckBaseline | null): AnomalyBaselineMeta {
  const payload = baseline?.kind === 'anomaly' ? baseline.baseline : undefined;
  const window = payload?.window;
  const seasonality = payload?.seasonality;
  return {
    window: typeof window === 'number' && window > 0 ? window : DEFAULT_ANOMALY_WINDOW,
    seasonality: seasonality === true,
  };
}

/**
 * The prior values a run happening right now (`now`) would actually be scored against — mirrors
 * `anomaly.py`'s `eligible_values(observations, now, params)` exactly: with seasonality on.
 */
function eligibleValues(
  observations: Observation[],
  meta: AnomalyBaselineMeta,
  now: Date,
): number[] {
  const considered = meta.seasonality
    ? observations.filter((o) => new Date(o.ts).getUTCDay() === now.getUTCDay())
    : observations;
  return considered.slice(-meta.window).map((o) => o.value);
}

function nextScoredAt(observations: Observation[], check: CheckTrendCheck): Date {
  const last = observations.at(-1);
  if (typeof check.config?.date_column !== 'string' || !last) return new Date();
  const next = new Date(last.ts);
  next.setUTCDate(next.getUTCDate() + (check.config.period === 'week' ? 7 : 1));
  return next;
}

export function CheckTrend({ suiteId, check, limit = 90, onAttached }: CheckTrendProps) {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const [promoted, setPromoted] = useState<string[]>([]);
  const isAnomaly = check.kind === 'anomaly';
  const perPeriod = isPerPeriod(check);

  const { state, reload } = useAsyncData(async () => {
    const [history, baseline, detected, calendar] = await Promise.all([
      listCheckHistory(suiteId, check.id, perPeriod ? 180 : limit),
      // Baseline is a debugging overlay, not the main story: a fetch failure (or simply no baseline
      // yet) degrades to "no overlay", never an error for the whole trend.
      isAnomaly
        ? getCheckBaseline(suiteId, check.id).catch(() => 'failed' as const)
        : Promise.resolve(null),
      // Likewise the seasonal overlays: without them the chart still shows the points.
      perPeriod
        ? getDetectedSeasonalPeriods(suiteId, check.id).catch(() => null)
        : Promise.resolve(null),
      perPeriod ? listSeasonalPeriods().catch(() => null) : Promise.resolve(null),
    ]);
    return { history, baseline, detected, calendar };
  });

  if (state.status === 'loading') return <Spin size="small" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load trend" description={state.error} />;
  }

  const { history, detected, calendar } = state.data;
  const baselineFailed = state.data.baseline === 'failed';
  const baseline = baselineFailed ? null : (state.data.baseline as CheckBaseline | null);
  const attached = [...attachedIds(check), ...promoted];
  const declared = (calendar ?? []).filter((p) => attached.includes(p.id));
  const withMetric = history.filter((p) => p.metric_value !== null);
  const bands = thresholdBands(check);
  const observations = parseObservations(baseline);
  const anomalyMeta = parseAnomalyMeta(baseline);

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
          {withMetric.length > 0 && (
            <StatusMarkerKey
              statuses={[...new Set(withMetric.map((p) => p.status as ResultStatus))]}
            />
          )}
          {isAnomaly && isColumnProfile(baseline) && (
            <Typography.Text
              type="secondary"
              style={{ display: 'block', marginTop: 12, fontSize: 12 }}
              data-testid="column-profile-baseline-note"
            >
              {`Column profile baseline — a separate history per column (null % and distinct count), ${columnProfileRuns(baseline)} run(s) recorded. The chart above is each run's largest deviation; a run's detail names the columns that moved.`}
            </Typography.Text>
          )}
          {isAnomaly && baselineFailed && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              title="The stored baseline could not be loaded"
              description="The learned band and the stored periods are not shown; the results above are."
            />
          )}
          {isAnomaly && !baselineFailed && !isColumnProfile(baseline) && (
            <AnomalyBaselinePanel
              observations={observations}
              meta={anomalyMeta}
              check={check}
              seasonal={
                perPeriod
                  ? {
                      history,
                      declared,
                      detected,
                      calendarMissing: calendar === null && attached.length > 0,
                      historyCapped: history.length >= 180,
                    }
                  : null
              }
            />
          )}
          {perPeriod && detected && (
            <div style={{ marginTop: 8 }}>
              <DetectedPeriodsList
                detected={detected}
                suiteId={suiteId}
                checkId={check.id}
                yearly={check.config?.yearly === true}
                onPromoted={(entryId, didAttach) => {
                  reload();
                  if (didAttach) {
                    setPromoted((ids) => [...ids, entryId]);
                    onAttached?.(entryId);
                  }
                }}
              />
            </div>
          )}
        </>
      ) : (
        <TrendTable
          points={history}
          observations={isAnomaly ? observations : null}
          judged={perPeriod ? resultsByPeriod(history) : null}
        />
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
            key={`${band.tier}${band.op}`}
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
            return <StatusMarker key={index} cx={cx} cy={cy} status={payload.status} />;
          }}
        />
      </LineChart>
    </ResponsiveChart>
  );
}

// ───────────────────────── anomaly baseline overlay (#593 debugger) ─

/**
 * Fallback z-multiplier when the check carries no `fail_threshold` — mean±2σ covers ~95% of a
 * normal distribution, a reasonable "this would look anomalous" default.
 */
const DEFAULT_BAND_K = 2;

interface SeasonalOverlay {
  history: CheckResultPoint[];
  declared: SeasonalPeriod[];
  detected: DetectedSeasonalPeriods | null;
  /** The calendar could not be read, though entries are attached. */
  calendarMissing: boolean;
  /** The history read hit its cap, so older judged periods have no band. */
  historyCapped: boolean;
}

function AnomalyBaselinePanel({
  observations,
  meta,
  check,
  seasonal,
}: {
  observations: Observation[];
  meta: AnomalyBaselineMeta;
  check: CheckTrendCheck;
  /** Set for a per-period check: the judged results and the calendar to draw beside the points. */
  seasonal: SeasonalOverlay | null;
}) {
  // The moment the next run would be scored at (`eligible_values` in anomaly.py). A per-period
  // check is scored at its period, not at the run: the next one is the period after the last
  // stored point, so a seasonal band is drawn for that weekday, not for today's.
  const now = nextScoredAt(observations, check);
  const eligible = eligibleValues(observations, meta, now);
  const seeded = observations.filter((o) => o.backfilled).length;
  const stats = meanStddev(eligible);
  const k =
    check.fail_threshold !== null && check.fail_threshold > 0
      ? check.fail_threshold
      : DEFAULT_BAND_K;
  // UTC to match the `getUTCDay()` filter in `eligibleValues` — a local-zone
  // label could name a different day than the one actually filtered on.
  const weekdayName = now.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'UTC' });
  const weekdaySuffix = meta.seasonality ? ` for ${weekdayName}s` : '';

  const shown = observations.slice(-BASELINE_POINTS_SHOWN);
  const spansYears =
    shown.length > 1 &&
    new Date(shown[shown.length - 1].ts).getTime() - new Date(shown[0].ts).getTime() > YEAR_MS;
  const data = shown.map((o) => ({
    label: spansYears ? dayWithYear(o.ts) : shortDay(o.ts),
    value: o.value,
  }));
  const yearly = check.config?.yearly === true;
  const judgedByDay = seasonal ? resultsByPeriod(seasonal.history) : null;
  const rows: PeriodRow[] = shown.map((o) => ({
    day: isoDay(o.ts),
    value: o.value,
    backfilled: o.backfilled,
    judged: judgedByDay?.get(isoDay(o.ts)) ?? null,
  }));
  const judged = rows.flatMap((r) => (r.judged ? [r.judged] : []));
  const formatDay = (day: string) =>
    spansYears ? dayWithYear(`${day}T00:00:00Z`) : shortDay(`${day}T00:00:00Z`);

  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {stats ? (
          <>
            Anomaly baseline — learned band{weekdaySuffix} (mean ± {k}σ) · μ={stats.mean.toFixed(2)}{' '}
            · σ={stats.stddev.toFixed(2)} · {eligible.length} points
            {seeded > 0 &&
              ` · ${seeded} of the ${observations.length} stored points were seeded from the table’s own rows, not measured by a run`}
            {observations.length > shown.length &&
              ` · the chart shows the most recent ${shown.length}`}
          </>
        ) : observations.length === 0 ? (
          // Matches the empty chart below it (#594 review): no observations at
          // all is never "learned band", it's simply nothing yet.
          'Anomaly baseline — no observations captured yet.'
        ) : (
          // At least one observation exists, but not the 2 the eligible (weekday-filtered, for a
          // seasonal check) subset needs for a defined stddev — still not a band.
          `Anomaly baseline — collecting observations${weekdaySuffix} (${eligible.length} so far; need at least 2 for a learned band).`
        )}
      </Typography.Text>
      {seasonal && (
        <SeasonalCaption
          yearly={yearly}
          k={k}
          judged={judged}
          seeded={seeded > 0}
          seasonal={seasonal}
          shown={rows.length > 0 ? { first: rows[0].day, last: rows[rows.length - 1].day } : null}
        />
      )}
      {seasonal ? (
        <PeriodBaselineChart
          rows={rows}
          k={k}
          declared={seasonal.declared}
          detected={seasonal.detected?.periods ?? []}
          formatDay={formatDay}
        />
      ) : (
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
      )}
    </div>
  );
}

/** What a per-period check's chart draws, in words: the band, the expected line, and why the
 *  yearly comparison did not apply where it did not. */
function SeasonalCaption({
  yearly,
  k,
  judged,
  seeded,
  seasonal,
  shown,
}: {
  yearly: boolean;
  k: number;
  judged: SeasonalPoint[];
  seeded: boolean;
  seasonal: SeasonalOverlay;
  /** The first and last plotted period. */
  shown: { first: string; last: string } | null;
}) {
  const { applied, notApplied } = yearlySummary(judged);
  const withheld = judged.filter((j) => !j.banded).length;
  const lines: string[] = [];
  if (judged.length === 0) {
    lines.push(
      'No shown period has a result yet, so no band is drawn on the chart: a band is what a run scored a period against.',
    );
  } else if (!yearly) {
    lines.push(
      `Shaded band: for each period a run judged (${judged.length} shown), the recent window it was scored against, mean ± ${k}σ.`,
    );
  } else {
    lines.push(
      `Shaded band: for each period a run judged (${judged.length} shown), the centre it was scored against ± ${k}× the spread. Where the yearly comparison applied (${applied} of ${judged.length}) the dashed line is the expected value, the level times what this time of year ran at in earlier years; the centre is that value shifted by how far earlier periods ran from what was expected of them, so the band need not sit on the line.`,
    );
    if (notApplied.length > 0) {
      const why = notApplied.map((n) => `${n.reason} (${n.count})`).join('; ');
      lines.push(
        `Not applied on ${judged.length - applied}, so those bands are the recent window and there is no expected value: ${why}.`,
      );
    }
  }
  if (seasonal.historyCapped) {
    lines.push(
      'Bands and expected values come from the most recent 180 results, so older periods shown have none.',
    );
  }
  if (withheld > 0) {
    lines.push(
      `${withheld} judged period${withheld === 1 ? ' fell' : 's fell'} inside a declared period with no earlier occurrence: a rise was expected and its size unknown, so no band is drawn there.`,
    );
  }
  if (seasonal.declared.length > 0) {
    const inView = new Set(
      shown
        ? declaredAreasBetween(seasonal.declared, shown.first, shown.last).map((a) => a.name)
        : [],
    );
    const outOfView = seasonal.declared.filter((d) => !inView.has(d.name)).map((d) => d.name);
    if (inView.size > 0) {
      lines.push(`Highlighted: the attached calendar entries (${[...inView].join(', ')}).`);
    }
    if (outOfView.length > 0) {
      lines.push(`Attached but with no occurrence in the periods shown: ${outOfView.join(', ')}.`);
    }
  } else if (seasonal.calendarMissing) {
    lines.push('The calendar could not be loaded, so attached entries are not highlighted.');
  }
  if (seeded) lines.push('Hollow points were seeded from the table; filled ones a run measured.');
  return (
    <Flex vertical gap={2} style={{ marginTop: 4 }} data-testid="seasonal-caption">
      {lines.map((line) => (
        <Typography.Text key={line} type="secondary" style={{ fontSize: 12 }}>
          {line}
        </Typography.Text>
      ))}
    </Flex>
  );
}

// ───────────────────────── a11y data-table fallback ─────────────────

function bandText(j: SeasonalPoint | undefined): string {
  if (!j) return '—';
  if (!j.banded) return 'no band (first occurrence of a declared period)';
  if (j.mean === null || j.stddev === null) return '—';
  return `${j.mean.toFixed(2)} ± ${j.stddev.toFixed(2)}`;
}

function expectedText(j: SeasonalPoint | undefined): string {
  if (!j) return '—';
  if (j.expected !== null) return j.expected.toFixed(2);
  return j.yearly_applied === false ? `not applied: ${yearlyReasonLabel(j.yearly_reason)}` : '—';
}

function TrendTable({
  points,
  observations,
  judged,
}: {
  points: CheckResultPoint[];
  observations: Observation[] | null;
  /** A per-period check's judged results by period, for the scored-against columns. */
  judged: Map<string, SeasonalPoint> | null;
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
            render: (s: ResultStatus) => <ResultStatusTag status={s} />,
          },
        ]}
      />
      {observations && observations.length > 0 && (
        <>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, marginTop: 12, display: 'block' }}
          >
            Anomaly baseline points, newest first
          </Typography.Text>
          <Table<Observation>
            size="small"
            rowKey={(o) => o.ts}
            pagination={
              observations.length > BASELINE_POINTS_SHOWN
                ? { pageSize: 50, showSizeChanger: false, size: 'small' }
                : false
            }
            dataSource={[...observations].reverse()}
            columns={[
              { title: 'Dated', dataIndex: 'ts', render: (v: string) => formatTimestamp(v) },
              { title: 'Value', dataIndex: 'value' },
              ...(judged
                ? [
                    {
                      title: 'Scored against (centre ± spread)',
                      key: 'band',
                      render: (_: unknown, o: Observation) => bandText(judged.get(isoDay(o.ts))),
                    },
                    {
                      title: 'Expected',
                      key: 'expected',
                      render: (_: unknown, o: Observation) =>
                        expectedText(judged.get(isoDay(o.ts))),
                    },
                    {
                      title: 'Declared period',
                      key: 'declared',
                      render: (_: unknown, o: Observation) =>
                        judged.get(isoDay(o.ts))?.declared_name ?? '—',
                    },
                  ]
                : []),
              ...(observations.some((o) => o.backfilled)
                ? [
                    {
                      title: 'Source',
                      dataIndex: 'backfilled',
                      // A seeded point is what the table says today about that period.
                      render: (b: boolean) => (b ? 'Seeded from the table' : 'Measured by a run'),
                    },
                  ]
                : []),
            ]}
          />
        </>
      )}
    </>
  );
}
