import type { AdminHealth, SecretSweepReport } from '../../api/admin';
import { PROVIDER_LABELS, type TriggerEnvNearMiss } from '../../api/triggerBindings';
import { formatTimestamp } from '../../components/results/resultsFormat';

/** `attention` = something is wrong. `unknown` = the signal exists but has observed nothing,
 *  which must never render as an all-clear (#828). */
export type SignalTone = 'attention' | 'unknown';

export interface OverviewSignal {
  key: string;
  tone: SignalTone;
  title: string;
  detail: string;
  /** The verb that takes the admin to the thing to fix. */
  verb: string;
  /** An app route, or a `#id` anchor when the fix lives on this page. */
  to: string;
}

const connectionLink = (id: string) => `/connections/${id}/edit`;

/** Poll staleness, credential rejections, beat heartbeat and queue depth, in that order. */
export function healthSignals(health: AdminHealth): OverviewSignal[] {
  const signals: OverviewSignal[] = [];

  for (const row of health.polling) {
    const provider = PROVIDER_LABELS[row.provider] ?? row.provider;
    const lastAttempt = row.last_polled_at
      ? `Last attempt ${formatTimestamp(row.last_polled_at)} (an attempt, not necessarily a success).`
      : 'It has never been polled.';
    if (row.status === 'stalled') {
      signals.push({
        key: `poll-${row.connection_id}`,
        tone: 'attention',
        title: `${provider} polling has stalled — ${row.name}`,
        detail: `${lastAttempt} While it is stalled, pipeline completions may not be reaching DataQ at all.`,
        verb: 'View connection',
        to: connectionLink(row.connection_id),
      });
    } else if (row.status === 'failing') {
      signals.push({
        key: `poll-${row.connection_id}`,
        tone: 'attention',
        title: `${provider} polling is failing — ${row.name}`,
        detail: `It is being polled on schedule, but recent attempts errored: ${
          row.last_error ?? 'no reason was recorded.'
        }`,
        verb: 'View connection',
        to: connectionLink(row.connection_id),
      });
    } else if (row.status === 'unknown') {
      signals.push({
        key: `poll-${row.connection_id}`,
        tone: 'unknown',
        title: `${provider} polling not monitored — ${row.name}`,
        detail:
          'It has never been polled since it was configured, so nothing has been observed about it. That is not the same as healthy.',
        verb: 'View connection',
        to: connectionLink(row.connection_id),
      });
    }
  }

  for (const row of health.credentials.filter((c) => c.status === 'failing')) {
    signals.push({
      key: `cred-${row.connection_id}`,
      tone: 'attention',
      title: `Stored credential rejected — ${row.name}`,
      detail: `${row.consecutive_auth_failures} consecutive authentication failure(s) on ${row.type} (${row.env}). ${
        row.last_error ?? 'No reason was recorded.'
      }`,
      verb: 'Re-auth',
      to: connectionLink(row.connection_id),
    });
  }

  const neverObserved = health.credentials.filter((c) => c.status === 'unknown').length;
  if (neverObserved > 0) {
    signals.push({
      key: 'cred-unknown',
      tone: 'unknown',
      title: `${neverObserved} datasource credential(s) never observed`,
      detail:
        'Credential health only moves when a run, dry-run, profile or connection test actually uses the credential. A connection nothing uses stays unknown — it has not been shown to work.',
      verb: 'View connections',
      to: '/connections',
    });
  }

  if (health.beat.status === 'stale') {
    signals.push({
      key: 'beat',
      tone: 'attention',
      title: 'Scheduler heartbeat is stale',
      detail: `Last tick ${health.beat.last_tick_at ? formatTimestamp(health.beat.last_tick_at) : 'unknown'}. Scheduled runs, orchestration polling and periodic sweeps may not be firing.`,
      verb: 'Details',
      to: '#scheduler',
    });
  } else if (health.beat.status === 'not_monitored') {
    signals.push({
      key: 'beat',
      tone: 'unknown',
      title: 'Scheduler heartbeat not monitored',
      detail:
        'The heartbeat task has never recorded a tick, so nothing is known about the scheduler — this is not a report that it is running.',
      verb: 'Details',
      to: '#scheduler',
    });
  }

  if (health.queues === null) {
    signals.push({
      key: 'queues',
      tone: 'unknown',
      title: 'Queue depth unknown',
      detail: `The broker could not be reached, so no depth is reported — it is not zero. ${
        health.queues_error ?? ''
      }`.trim(),
      verb: 'Details',
      to: '#scheduler',
    });
  }

  return signals;
}

/** The orphan-secret sweep, whose three non-`recorded` shapes must never read as "0 orphans". */
export function sweepSignals(sweep: SecretSweepReport): OverviewSignal[] {
  if (sweep.status === 'never_run') {
    return [
      {
        key: 'sweep',
        tone: 'unknown',
        title: 'Orphan-secret sweep has never run',
        detail:
          'No report has been recorded, so the number of unowned secrets in the store is unknown — not zero.',
        verb: 'Review',
        to: '#secret-store',
      },
    ];
  }
  if (sweep.status === 'skipped' || sweep.orphan_count === null) {
    return [
      {
        key: 'sweep',
        tone: 'unknown',
        title: 'Orphan-secret sweep did not enumerate the store',
        detail: `The last run recorded no count, so nothing is known about unowned secrets. ${
          sweep.error ?? ''
        }`.trim(),
        verb: 'Review',
        to: '#secret-store',
      },
    ];
  }
  if (sweep.orphan_count > 0) {
    return [
      {
        key: 'sweep',
        tone: 'attention',
        title: `Orphan-secret sweep found ${sweep.orphan_count} unowned secret(s)`,
        detail: `Recorded ${sweep.ran_at ? formatTimestamp(sweep.ran_at) : 'at an unknown time'}. Each is a stored credential no connection references any more.`,
        verb: 'Review',
        to: '#secret-store',
      },
    ];
  }
  return [];
}

/** Env mismatches: a pipeline succeeds, but no enabled binding matches the env it ran in. */
export function nearMissSignals(rows: TriggerEnvNearMiss[]): OverviewSignal[] {
  return rows.map((row) => ({
    key: `near-miss-${row.provider}-${row.pipeline_or_dag_id}-${row.run_env}`,
    tone: 'attention' as const,
    title: `Trigger env mismatch — ${row.pipeline_or_dag_id}`,
    detail: `Succeeded runs keep landing in ${row.run_env}, but the only enabled binding targets ${row.binding_env}, so no suite is triggered. Only mismatches on suites you can see are listed.`,
    verb: 'View suites',
    to: '/suites',
  }));
}

/** A source that failed to load is its own signal — never a quietly missing row. */
export function sourceErrorSignal(
  key: string,
  source: string,
  error: string,
  to: string,
): OverviewSignal {
  return {
    key: `error-${key}`,
    tone: 'unknown',
    title: `${source} could not be loaded`,
    detail: `${error} — nothing it would have reported is being shown, so treat this as unknown rather than clear.`,
    verb: 'Retry',
    to,
  };
}

/** Attention first, then unknown; stable within each group. */
export function orderSignals(signals: OverviewSignal[]): OverviewSignal[] {
  return [
    ...signals.filter((s) => s.tone === 'attention'),
    ...signals.filter((s) => s.tone === 'unknown'),
  ];
}
