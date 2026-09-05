import { useCallback, useState } from 'react';

import { type AuditChainStatus, verifyAuditChain } from '../../api/admin';
import { type FetchFailure, fetchFailure } from '../../utils/errors';

/** How each chain verdict is labelled — `empty` is deliberately not "intact". */
export const STATUS_TAG: Record<AuditChainStatus['status'], { color: string; label: string }> = {
  ok: { color: 'green', label: 'Intact' },
  broken: { color: 'red', label: 'Broken' },
  empty: { color: 'default', label: 'Nothing to verify' },
};

/** Verification walks the whole hashed set, so it is never run on mount — every consumer
 *  opens in `idle` and stays there until an admin asks for it. */
export type VerifyState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'done'; result: AuditChainStatus; checkedAt: string }
  | { status: 'failed'; failure: FetchFailure };

/** Shared by the Compliance chain card and the Overview health checklist, so the two can't
 *  drift on the one question where a wrong answer is worst. */
export function useAuditChainVerify(): { state: VerifyState; verify: () => Promise<void> } {
  const [state, setState] = useState<VerifyState>({ status: 'idle' });

  const verify = useCallback(async () => {
    setState({ status: 'running' });
    try {
      const result = await verifyAuditChain();
      setState({ status: 'done', result, checkedAt: new Date().toISOString() });
    } catch (err) {
      // A failed verification is never rendered as an intact chain.
      setState({ status: 'failed', failure: fetchFailure(err) });
    }
  }, []);

  return { state, verify };
}
