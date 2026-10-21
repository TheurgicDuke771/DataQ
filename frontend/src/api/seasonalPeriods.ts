import { api } from './client';

/** The declared seasonal calendar (ADR 0048): read by anyone signed in, changed by admins. */

export type SeasonalPeriodKind = 'recurring' | 'dated';

/** Mirrors `SeasonalPeriodRead`. A recurring entry sets the month/day fields, a dated one
 *  `start_date`/`end_date` (ISO dates) and optionally a `series`. */
export interface SeasonalPeriod {
  id: string;
  name: string;
  kind: SeasonalPeriodKind;
  start_month: number | null;
  start_day: number | null;
  end_month: number | null;
  end_day: number | null;
  start_date: string | null;
  end_date: string | null;
  series: string | null;
  note: string | null;
  /** Checks whose config names this entry; deleting is refused while above zero. */
  attached_checks: number;
  created_at: string;
  updated_at: string;
}

export type SeasonalPeriodWrite = Pick<
  SeasonalPeriod,
  | 'name'
  | 'kind'
  | 'start_month'
  | 'start_day'
  | 'end_month'
  | 'end_day'
  | 'start_date'
  | 'end_date'
  | 'series'
  | 'note'
>;

/** One check that still names an entry, from a refused delete's `detail.checks`. */
export interface AttachedCheck {
  id: string;
  name: string;
  suite_id: string;
  suite_name: string;
}

const BASE = '/admin/seasonal-periods';

export async function listSeasonalPeriods(): Promise<SeasonalPeriod[]> {
  const { data } = await api.get<SeasonalPeriod[]>(BASE);
  return data;
}

export async function createSeasonalPeriod(payload: SeasonalPeriodWrite): Promise<SeasonalPeriod> {
  const { data } = await api.post<SeasonalPeriod>(BASE, payload);
  return data;
}

export async function updateSeasonalPeriod(
  id: string,
  payload: SeasonalPeriodWrite,
): Promise<SeasonalPeriod> {
  const { data } = await api.patch<SeasonalPeriod>(`${BASE}/${id}`, payload);
  return data;
}

/** Rejects with a `seasonal_period_in_use` 409 whose `detail.checks` names the checks. */
export async function deleteSeasonalPeriod(id: string): Promise<void> {
  await api.delete(`${BASE}/${id}`);
}

export interface PromotePayload {
  suite_id: string;
  check_id: string;
  /** The detected period's `start` (its most recent season). */
  start: string;
  name: string;
  series?: string | null;
  note?: string | null;
  attach: boolean;
}

export async function promoteDetectedPeriod(payload: PromotePayload): Promise<SeasonalPeriod> {
  const { data } = await api.post<SeasonalPeriod>(`${BASE}/promote`, payload);
  return data;
}

// ───────────────────────── detected periods (a check's own history) ─────

export interface DetectedOccurrence {
  year: number;
  start: string;
  end: string;
  peak_ratio: number;
  periods: number;
  backfilled_periods: number;
}

export interface DetectedPeriod {
  kind: 'recurring' | 'seen_once';
  start: string;
  end: string;
  peak_ratio: number;
  years_seen: number;
  range: { start_month: number; start_day: number; end_month: number; end_day: number } | null;
  occurrences: DetectedOccurrence[];
  /** Attached calendar entries that overlap it. */
  declared: { id: string; name: string }[];
}

/** Mirrors `DetectedSeasonalPeriodsRead`. `reason` is set when there was nothing to look at. */
export interface DetectedSeasonalPeriods {
  reason: string | null;
  periods: DetectedPeriod[];
  thresholds: {
    ratio: number;
    min_consecutive: number;
    same_time_days: number;
    level: 'median' | 'mean' | null;
  };
  history: {
    unit: string | null;
    points: number;
    backfilled: number;
    evaluated: number;
    first: string | null;
    last: string | null;
    years: number;
  };
}

export async function getDetectedSeasonalPeriods(
  suiteId: string,
  checkId: string,
): Promise<DetectedSeasonalPeriods> {
  const { data } = await api.get<DetectedSeasonalPeriods>(
    `/suites/${suiteId}/checks/${checkId}/seasonal-periods/detected`,
  );
  return data;
}

// ───────────────────────── display helpers ─────────────────────────

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

export const MONTH_NAMES = MONTHS;

function monthDay(month: number | null, day: number | null): string {
  return month && day ? `${day} ${MONTHS[month - 1]}` : '?';
}

/** `20 Dec – 5 Jan, every year` or `2026-11-27 – 2026-11-30`. */
export function describeSpan(p: Pick<SeasonalPeriod, SeasonalPeriodKindFields>): string {
  if (p.kind === 'recurring') {
    return `${monthDay(p.start_month, p.start_day)} – ${monthDay(p.end_month, p.end_day)}, every year`;
  }
  return `${p.start_date ?? '?'} – ${p.end_date ?? '?'}`;
}

type SeasonalPeriodKindFields =
  'kind' | 'start_month' | 'start_day' | 'end_month' | 'end_day' | 'start_date' | 'end_date';

const ISO = (y: number, m: number, d: number) =>
  `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

function lastDayOf(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * The occurrences of an entry that touch `[first, last]` (ISO dates), as inclusive ISO ranges.
 * A recurring range may wrap the year end; 29 February is 28 February in other years.
 */
export function occurrencesBetween(
  p: Pick<SeasonalPeriod, SeasonalPeriodKindFields>,
  first: string,
  last: string,
): { start: string; end: string }[] {
  if (p.kind === 'dated') {
    if (!p.start_date || !p.end_date) return [];
    return p.end_date < first || p.start_date > last
      ? []
      : [{ start: p.start_date, end: p.end_date }];
  }
  if (!p.start_month || !p.start_day || !p.end_month || !p.end_day) return [];
  const wraps = p.end_month * 100 + p.end_day < p.start_month * 100 + p.start_day;
  const out: { start: string; end: string }[] = [];
  const firstYear = Number(first.slice(0, 4)) - 1;
  const lastYear = Number(last.slice(0, 4));
  for (let year = firstYear; year <= lastYear; year += 1) {
    const endYear = wraps ? year + 1 : year;
    const start = ISO(year, p.start_month, Math.min(p.start_day, lastDayOf(year, p.start_month)));
    const end = ISO(endYear, p.end_month, Math.min(p.end_day, lastDayOf(endYear, p.end_month)));
    if (end >= first && start <= last) out.push({ start, end });
  }
  return out;
}
