import { createContext } from 'react';

import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';

/**
 * The `/me` response (identity + `is_workspace_admin`) shared across the tree, so
 * the nav gate and the pages read one fetch rather than each calling `/me`.
 * Defaults to `loading` until the provider resolves it.
 */
export const MeContext = createContext<AsyncState<MeResponse>>({ status: 'loading' });

/**
 * Lets a component that just PATCHed `/me` (profile completion prompt, the
 * Profile page's own editor — #1139) push the fresh response into `MeContext`
 * directly, rather than triggering a second `GET /me` round trip. A no-op
 * default so a stray consumer outside `MeProvider` fails silently rather than
 * throwing.
 */
export const MeUpdateContext = createContext<(me: MeResponse) => void>(() => {});
