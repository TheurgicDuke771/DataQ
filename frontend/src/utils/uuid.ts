const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isUuid = (value: string | null | undefined): value is string =>
  typeof value === 'string' && UUID_RE.test(value);

/** A deep-link id that is not a well-formed UUID is treated as absent (null). */
export const uuidOrNull = (value: string | null | undefined): string | null =>
  isUuid(value) ? value : null;
