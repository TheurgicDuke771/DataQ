import { Tooltip } from 'antd';
import type { ColumnType } from 'antd/es/table';

/**
 * A bounded-width, single-line-ellipsis `ColumnType` for a free-text field
 * that can be arbitrarily long — a provider error string, a failure reason,
 * a message. Without a width + ellipsis, one long unbroken string stretches
 * the whole column (and the table's horizontal scrollbar with it) to fit on
 * one line, which is exactly what made the pipeline-runs "Failure reason"
 * column unusable (#1184).
 *
 * `ellipsis: { showTitle: false }` suppresses antd's own native-`title`
 * hover (plain browser tooltip, no styling) so the custom `Tooltip` below is
 * the only one — antd's documented pattern for a styled hover reveal. The
 * `null`/`undefined` placeholder (`'—'`) renders un-tooltipped: there's
 * nothing to reveal.
 */
export function ellipsisColumn<T>(
  title: string,
  dataIndex: keyof T & string,
  width: number,
): ColumnType<T> {
  return {
    title,
    dataIndex,
    width,
    ellipsis: { showTitle: false },
    render: (value: string | null | undefined) =>
      value === null || value === undefined ? (
        '—'
      ) : (
        <Tooltip title={value}>
          <span>{value}</span>
        </Tooltip>
      ),
  };
}
