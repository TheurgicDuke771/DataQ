/**
 * Tagged template for API request paths: every interpolated value is
 * percent-encoded as a single path segment, so an id taken from the URL can
 * never add segments (`/`, `..`) or a query/fragment (`?`, `#`).
 *   api.get(apiPath`/incidents/${id}/ack`)
 */
export function apiPath(strings: TemplateStringsArray, ...values: Array<string | number>): string {
  return strings.reduce(
    (out, s, i) => out + s + (i < values.length ? encodeURIComponent(String(values[i])) : ''),
    '',
  );
}
