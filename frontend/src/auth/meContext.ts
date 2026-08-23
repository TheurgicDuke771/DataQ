import { createContext } from 'react';

import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';

/**
 * The `/me` response (identity + `is_workspace_admin`) shared across the tree, so the nav gate and
 * the pages read one fetch rather than each calling `/me`.
 */
export const MeContext = createContext<AsyncState<MeResponse>>({ status: 'loading' });

/**
 * Lets a component that just PATCHed `/me` (profile completion prompt, the Profile page's own
 * editor — #1139) push the fresh response into `MeContext` directly.
 */
export const MeUpdateContext = createContext<(me: MeResponse) => void>(() => {});
