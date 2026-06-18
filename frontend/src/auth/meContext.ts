import { createContext } from 'react';

import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';

/**
 * The `/me` response (identity + `is_workspace_admin`) shared across the tree, so
 * the nav gate and the pages read one fetch rather than each calling `/me`.
 * Defaults to `loading` until the provider resolves it.
 */
export const MeContext = createContext<AsyncState<MeResponse>>({ status: 'loading' });
