import { Typography } from 'antd';

import { formatScalar } from './resultsFormat';

/**
 * Render a GX observed/expected value (or any unknown scalar/JSON blob) the way the Results table
 * and the dry-run preview both want it: an em dash for null/undefined.
 */
export function ScalarValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <>—</>;
  return <Typography.Text code>{formatScalar(value)}</Typography.Text>;
}
