import { matchPath } from 'react-router-dom';

import { api } from './client';
import { IS_DEMO } from '../demo/flag';

/** One event's attributes and what each may hold: a list of allowed values, or `"integer"`. */
export interface TelemetryEventSpec {
  name: string;
  attributes: Record<string, string[] | 'integer'>;
}

/** `GET /admin/telemetry`. `enabled` is the effective state: the stored opt-in AND a sink the
 *  operator configured. With `sink = off` the opt-in is stored but sends nothing. */
export interface TelemetrySettings {
  enabled: boolean;
  stored: boolean;
  sink: 'off' | 'file' | 'otlp';
  /** False when a sink is configured but could not be started. */
  sink_ready: boolean;
  events: TelemetryEventSpec[];
  updated_by: string | null;
  updated_at: string | null;
}

export async function getTelemetrySettings(): Promise<TelemetrySettings> {
  const { data } = await api.get<TelemetrySettings>('/admin/telemetry');
  return data;
}

export async function putTelemetrySettings(enabled: boolean): Promise<TelemetrySettings> {
  const { data } = await api.put<TelemetrySettings>('/admin/telemetry', { enabled });
  return data;
}

/** Route patterns → the route NAME reported. A URL is never sent, so no id can leave. */
const ROUTE_NAMES: [string, string][] = [
  ['/dashboard', 'dashboard'],
  ['/connections', 'connections'],
  ['/connections/new', 'connection_new'],
  ['/connections/:id/edit', 'connection_edit'],
  ['/suites', 'suites'],
  ['/suites/new', 'suite_new'],
  ['/suites/:id', 'suite_detail'],
  ['/suites/:id/edit', 'suite_edit'],
  ['/suites/:id/checks/new', 'check_new'],
  ['/suites/:id/checks/:checkId/edit', 'check_edit'],
  ['/assets', 'assets'],
  ['/assets/:id', 'asset_detail'],
  ['/results', 'results'],
  ['/results/:id', 'run_detail'],
  ['/profile', 'profile'],
  ['/admin/overview', 'admin_overview'],
  ['/admin/members', 'admin_members'],
  ['/admin/suites', 'admin_suites'],
  ['/admin/settings', 'admin_settings'],
  ['/admin/compliance', 'admin_compliance'],
  ['/admin/integrations', 'admin_integrations'],
];

export function routeName(pathname: string): string {
  const hit = ROUTE_NAMES.find(([pattern]) => matchPath(pattern, pathname));
  return hit ? hit[1] : 'other';
}

type ClientEvent =
  | { name: 'page_viewed'; attributes: { route: string } }
  | { name: 'check_editor_abandoned'; attributes: { step: 'expectation' | 'config' } };

let status: Promise<boolean> | null = null;

/** Whether the server will record browser events at all; asked once per page load. A failed
 *  ask (signed out, offline) is not remembered, so the next navigation asks again. */
function telemetryEnabled(): Promise<boolean> {
  status ??= api
    .get<{ enabled: boolean }>('/telemetry')
    .then(({ data }) => data.enabled)
    .catch(() => {
      status = null;
      return false;
    });
  return status;
}

/** Fire-and-forget: a telemetry failure never reaches the user. */
export function reportEvent(event: ClientEvent): void {
  if (IS_DEMO) return;
  void telemetryEnabled().then((enabled) => {
    if (!enabled) return;
    api.post('/telemetry/events', { events: [event] }).catch(() => undefined);
  });
}

/** Test seam: forget the cached status. */
export function resetTelemetryStatus(): void {
  status = null;
}
