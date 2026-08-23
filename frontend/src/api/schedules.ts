import { api } from './client';

/** Schedules — cron-driven suite runs (A7). */

/** Mirrors the backend `ScheduleRead`. */
export interface Schedule {
  id: string;
  suite_id: string;
  cron: string;
  timezone: string;
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
}

/** Mirrors `ScheduleCreate`. */
export interface ScheduleCreate {
  suite_id: string;
  cron: string;
  timezone?: string;
  enabled?: boolean;
}

/** Mirrors `ScheduleUpdate` — partial; only supplied fields change. */
export interface ScheduleUpdate {
  cron?: string;
  timezone?: string;
  enabled?: boolean;
}

export async function listSchedules(suiteId: string): Promise<Schedule[]> {
  const { data } = await api.get<Schedule[]>('/schedules', { params: { suite_id: suiteId } });
  return data;
}

export async function createSchedule(payload: ScheduleCreate): Promise<Schedule> {
  const { data } = await api.post<Schedule>('/schedules', payload);
  return data;
}

export async function updateSchedule(id: string, payload: ScheduleUpdate): Promise<Schedule> {
  const { data } = await api.patch<Schedule>(`/schedules/${id}`, payload);
  return data;
}

export async function deleteSchedule(id: string): Promise<void> {
  await api.delete(`/schedules/${id}`);
}

/** IANA timezones for the schedule editor. */
export function timezoneOptions(): string[] {
  const supported =
    typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  const zones = supported.length > 0 ? supported : ['UTC'];
  return ['UTC', ...zones.filter((z) => z !== 'UTC')];
}
