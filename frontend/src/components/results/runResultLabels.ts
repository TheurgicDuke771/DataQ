import type { RunStatus } from '../../api/runs';

/**
 * What to say when a run has no result rows. The reason depends on the run: a check's results
 * are deleted with the check, so a run that completed can end up with none.
 */
export function noResultsMessage(status: RunStatus): string {
  if (status === 'succeeded') {
    return 'This run completed, but none of its check results remain. A check’s results are removed when the check is deleted.';
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
