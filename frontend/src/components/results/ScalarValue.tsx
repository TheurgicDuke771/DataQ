import { Typography } from 'antd';

import { formatScalar } from './resultsFormat';

/**
 * Render a GX observed/expected value (or any unknown scalar/JSON blob) the way
 * the Results table and the dry-run preview both want it: an em dash for
 * null/undefined, otherwise the `formatScalar` string in a monospace `code` box.
 * Shared so the `null ? '—' : <Text code>{JSON.stringify(...)}</Text>` pattern
 * lives in one place (#231); `formatScalar` also renders falsy scalars (`0`,
 * `false`, `''`) as themselves instead of collapsing them to the em dash.
 */
export function ScalarValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <>—</>;
  return <Typography.Text code>{formatScalar(value)}</Typography.Text>;
}
