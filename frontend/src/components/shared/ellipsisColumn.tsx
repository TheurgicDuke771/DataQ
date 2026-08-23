import { Tooltip } from 'antd';
import type { ColumnType } from 'antd/es/table';
import type { CSSProperties } from 'react';

/** The inline style that ACTUALLY bounds a long-text table cell. */
export function boundedTextStyle(maxWidth: number): CSSProperties {
  return {
    display: 'inline-block',
    maxWidth,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    // Without this the inline-block sits on the text baseline and leaves a
    // descender gap under the row.
    verticalAlign: 'bottom',
  };
}

/**
 * A bounded-width, single-line-ellipsis `ColumnType` for a free-text field that can be arbitrarily
 * long — a provider error string, a failure reason, a message.
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
          <span style={boundedTextStyle(width)}>{value}</span>
        </Tooltip>
      ),
  };
}
