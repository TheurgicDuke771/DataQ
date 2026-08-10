import { Tooltip } from 'antd';
import type { ColumnType } from 'antd/es/table';
import type { CSSProperties } from 'react';

/**
 * The inline style that ACTUALLY bounds a long-text table cell.
 *
 * The obvious spelling — a `width` + `ellipsis` on the antd column — is inert
 * in every DataQ table (#1282). All of them render `scroll={{ x: 'max-content'
 * }}`, which rc-table turns into `style="width: max-content"` on the `<table>`;
 * a CSS intrinsic-sizing keyword sizes the table from its content, which
 * neuters `table-layout: fixed`, which in turn demotes the `<colgroup>` width
 * to a non-binding hint. The cell then grows to fit the whole string on one
 * line and `text-overflow: ellipsis` never fires, because nothing overflows.
 * (rc-table carries its own note on the interaction — ant-design#25227.)
 *
 * Bounding the *inner* element sidesteps that entirely: an inline-block with a
 * `max-width` caps the cell's max-content contribution, so the column stays put
 * and the text clips, whatever the table's own width resolves to. It is also
 * the only variant that survives a future change of `scroll.x`.
 *
 * NOTE for anyone verifying a change here: jsdom performs no layout, so no
 * Vitest assertion can tell a working bound from an inert one — that is exactly
 * how #1282 shipped twice and stayed live. The real guard is the Playwright
 * measurement in `e2e/results.spec.ts`, which compares rendered widths.
 */
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
 * A bounded-width, single-line-ellipsis `ColumnType` for a free-text field
 * that can be arbitrarily long — a provider error string, a failure reason,
 * a message. Without a bound, one long unbroken string stretches the whole
 * column (and the table's horizontal scrollbar with it) to fit on one line,
 * which is exactly what made the pipeline-runs "Failure reason" column
 * unusable (#1184).
 *
 * The bound that does the work is `boundedTextStyle` on the rendered span; the
 * column's own `width` is kept as the honest declaration of intent (and would
 * bind if a table ever drops `scroll.x = 'max-content'`), and `ellipsis: {
 * showTitle: false }` is kept because it suppresses antd's own native-`title`
 * hover so the custom `Tooltip` below is the only one. The `null`/`undefined`
 * placeholder (`'—'`) renders un-tooltipped and unbounded: there's nothing to
 * reveal and nothing to clip.
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
