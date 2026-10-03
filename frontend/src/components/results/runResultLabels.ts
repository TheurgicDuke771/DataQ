import type { RunStatus } from '../../api/runs';

/**
 * What to say when a run has no result rows. The reason depends on the run: a check's results
 * are deleted with the check, so a run that completed can end up with none — and a suite with
 * no checks also completes with none, which the run itself cannot tell apart.
 */
export function noResultsMessage(status: RunStatus): string {
  if (status === 'succeeded') {
    return 'This run completed and has no check results. Either the suite had no checks when it ran, or its checks have been deleted since: a check’s results are removed with it.';
  }
  if (status === 'queued' || status === 'running') {
    return 'No check results yet — the run has not finished.';
  }
  return 'No check results — the run did not complete.';
}

/**
 * The name to show for a result's check. A check missing from a list that DID load no longer
 * exists; when the list could not be loaded, nothing is known, so only the id is shown.
 */
export function checkLabel(
  name: string | undefined,
  checkId: string,
  checksKnown: boolean,
): string {
  if (name !== undefined) return name;
  const shortId = checkId.slice(0, 8);
  return checksKnown ? `Deleted check (${shortId})` : shortId;
}

/** `checkLabel` for an export: the FULL id, so a row can still be joined to a check downstream. */
export function checkExportName(
  name: string | undefined,
  checkId: string,
  checksKnown: boolean,
): string {
  if (name !== undefined) return name;
  return checksKnown ? `Deleted check (${checkId})` : checkId;
}
