import axios from 'axios';

import { errorMessage } from './errors';

/**
 * Map a backend 422 onto a form field, so a refusal lands where the author can
 * act on it instead of in a toast that dismisses itself.
 *
 * The repo's own principle — every client-side refusal in the suite/check editors
 * uses inline `form.setFields` — stopped at the API boundary: a 422 the backend
 * raised surfaced through `useAsyncAction`'s generic handler as a transient
 * message, with nothing marked. That is worst for the errors that name a
 * *conflict between two things*, where the reader has to hold the sentence in
 * their head while going to look at the other one.
 *
 * Deliberately narrow. It maps only errors the caller opts into by code, and
 * falls through to the generic path for everything else — a blanket "put any 422
 * on some field" would guess wrong the moment the backend grows an error whose
 * detail shape it hasn't seen.
 */
export interface ApiFieldError {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

/** The `{error: {code, message, detail}}` envelope, or `undefined` if this isn't
 *  one — a network failure or a non-DataQError response. */
export function apiFieldError(err: unknown): ApiFieldError | undefined {
  if (!axios.isAxiosError(err)) return undefined;
  const envelope = (err.response?.data as { error?: Partial<ApiFieldError> } | undefined)?.error;
  if (typeof envelope?.code !== 'string') return undefined;
  return {
    code: envelope.code,
    // The interceptor has already swapped the envelope message onto
    // `error.message`; prefer the envelope's own so this doesn't depend on that.
    message: typeof envelope.message === 'string' ? envelope.message : errorMessage(err),
    detail: (envelope.detail as Record<string, unknown>) ?? {},
  };
}

/** A `detail` value that is a list of names (e.g. the conflicting checks), or
 *  `[]` — so a caller can name the obstacle rather than say "something conflicts". */
export function detailNames(detail: Record<string, unknown>, key: string): string[] {
  const value = detail[key];
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}
