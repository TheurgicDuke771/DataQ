// Under-reporting guard for the a11y scans (#2051). The ratchet fails only on violations that
// are NEW, so a scan that saw less page than it should have — a table still behind its loading
// state — always passes, and with an empty baseline there is nothing a missing row could be
// compared to. What a half-loaded scan does leave behind is a smaller page: axe evaluates fewer
// nodes. So a scan is repeated until the number of nodes it evaluates stops growing, and a page
// that is still growing when the attempts run out fails rather than passing on what it saw.
// Kept framework-free so the Vitest unit test can exercise it without a browser.

/** The part of an axe result this guard reads: every rule outcome lists the nodes it judged. */
export interface AxeCoverageLike {
  passes: Array<{ nodes: unknown[] }>;
  violations: Array<{ nodes: unknown[] }>;
  incomplete: Array<{ nodes: unknown[] }>;
}

/** How many node evaluations a scan made, across passes, violations and incomplete results. */
export function evaluatedNodes(result: AxeCoverageLike): number {
  return [...result.passes, ...result.violations, ...result.incomplete].reduce(
    (sum, rule) => sum + rule.nodes.length,
    0,
  );
}

/** Growth beyond this share of the earlier scan means the page was still loading. Below it is
 *  noise: a tooltip or a relative timestamp re-rendering between two scans. */
export const GROWTH_TOLERANCE = 0.05;

/** Whether ``later`` saw materially more page than ``earlier``. */
export function stillLoading(earlier: number, later: number): boolean {
  return later > earlier * (1 + GROWTH_TOLERANCE);
}

export interface SettledScan<T> {
  result: T;
  /** Node evaluations of each scan made, in order — the failure message shows the trend. */
  counts: number[];
  /** False when the page was still growing on the last attempt. */
  settled: boolean;
}

/** Run ``scan`` (after ``settle``) until two consecutive scans evaluate about the same number of
 *  nodes, up to ``attempts`` scans. Returns the last result, which saw the most page. */
export async function scanUntilStable<T extends AxeCoverageLike>(
  scan: () => Promise<T>,
  settle: () => Promise<void>,
  attempts = 4,
): Promise<SettledScan<T>> {
  await settle();
  let result = await scan();
  const counts = [evaluatedNodes(result)];
  for (let i = 1; i < attempts; i++) {
    await settle();
    result = await scan();
    counts.push(evaluatedNodes(result));
    if (!stillLoading(counts[counts.length - 2], counts[counts.length - 1])) {
      return { result, counts, settled: true };
    }
  }
  return { result, counts, settled: false };
}

export function stillLoadingMessage(surface: string, counts: number[]): string {
  return (
    `${surface}: the page was still loading when it was scanned — axe evaluated ` +
    `${counts.join(' → ')} nodes over ${counts.length} scans, so a scan would judge only part of ` +
    'it. Wait for the content the route shows before checkRoute(), or find what keeps loading.'
  );
}
