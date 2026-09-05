import { type SecretSweepReport, getSecretSweep } from '../../api/admin';

export const SWEEP_POLL_ATTEMPTS = 10;
export const SWEEP_POLL_INTERVAL_MS = 3000;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Re-read the sweep report until `ran_at` moves past `previousRanAt`. Bounded: a worker that is
 * down or busy must end in an honest "still queued", never in an unbounded spinner and never in a
 * stale report presented as the new run's result.
 */
export async function awaitSweepRun(
  previousRanAt: string | null,
  options: {
    attempts?: number;
    intervalMs?: number;
    sleep?: (ms: number) => Promise<unknown>;
    read?: () => Promise<SecretSweepReport>;
  } = {},
): Promise<SecretSweepReport | null> {
  const {
    attempts = SWEEP_POLL_ATTEMPTS,
    intervalMs = SWEEP_POLL_INTERVAL_MS,
    sleep = wait,
    read = getSecretSweep,
  } = options;

  for (let i = 0; i < attempts; i += 1) {
    await sleep(intervalMs);
    const report = await read();
    if (report.ran_at && report.ran_at !== previousRanAt) return report;
  }
  return null;
}
