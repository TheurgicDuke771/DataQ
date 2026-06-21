import { App } from 'antd';
import { useRef, useState } from 'react';

import { type Run, runSuite } from '../api/runs';
import type { Suite } from '../api/suites';

/**
 * Trigger a manual run of a suite — the shared logic behind the suite-detail Run
 * button and the cross-suite Run-now panel. Owns the in-flight `running` state, a
 * ref double-guard (so a synchronous double-click can't dispatch two runs in the
 * tick before the button disables), and the success / error toasts. `onQueued`
 * receives the queued `Run` (e.g. to open the live-progress drawer on it).
 *
 * Must be called inside an antd `App` context (uses `App.useApp` for messages).
 */
export function useRunTrigger(onQueued: (run: Run, suite: Suite) => void): {
  running: boolean;
  run: (suite: Suite) => Promise<void>;
} {
  const { message } = App.useApp();
  const [running, setRunning] = useState(false);
  const runningRef = useRef(false);

  const run = async (suite: Suite) => {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    try {
      const queued = await runSuite(suite.id);
      message.success(`${suite.name}: run queued`);
      onQueued(queued, suite);
    } catch (err) {
      message.error(`Run failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

  return { running, run };
}
