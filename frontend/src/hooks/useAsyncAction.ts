import { useCallback, useState } from 'react';
import { App } from 'antd';

import { errorMessage } from '../utils/errors';

/**
 * The `setSubmitting(true)` → `try { … } catch { message.error } finally { setSubmitting(false) }`
 * scaffold that recurred across every mutating form (connection/suite/check save, import.
 */
export function useAsyncAction(errorPrefix = 'Action failed'): {
  run: (action: () => Promise<void>) => Promise<void>;
  loading: boolean;
} {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async (action: () => Promise<void>) => {
      setLoading(true);
      try {
        await action();
      } catch (err) {
        message.error(`${errorPrefix}: ${errorMessage(err)}`);
      } finally {
        setLoading(false);
      }
    },
    [message, errorPrefix],
  );

  return { run, loading };
}
