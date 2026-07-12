import { DownloadOutlined } from '@ant-design/icons';
import { App, Button, Descriptions, Flex, Table, Typography } from 'antd';
import { useState } from 'react';

import { downloadComparisonReport, type Result } from '../../api/runs';

/** The comparison bucket keys, in stable display order (ADR 0015 §4). */
const BUCKETS = [
  { key: 'mismatched', label: 'Mismatched' },
  { key: 'additional_in_source', label: 'Only in source' },
  { key: 'additional_in_target', label: 'Only in target' },
] as const;

const COUNT_LABELS: [string, string][] = [
  ['source_rows', 'Source rows'],
  ['target_rows', 'Target rows'],
  ['matched', 'Matched'],
  ['mismatched', 'Mismatched'],
  ['additional_in_source', 'Only in source'],
  ['additional_in_target', 'Only in target'],
  // Value-grain counters (comparison:columns, #799) — presence-filtered like
  // the row-grain ones, so each grain shows only its own set.
  ['matched_values', 'Matched values'],
  ['mismatched_values', 'Mismatched values'],
  ['additional_in_source_values', 'Values only in source'],
  ['additional_in_target_values', 'Values only in target'],
  ['mismatch_percent', 'Mismatch %'],
];

const PER_COLUMN_BUCKETS: [string, string][] = [
  ['matched', 'Matched'],
  ['mismatched', 'Mismatched'],
  ['additional_in_source', 'Only in source'],
  ['additional_in_target', 'Only in target'],
];

/**
 * Expanded-row detail for a comparison result (ADR 0015 §4): the bucket counts,
 * the redacted per-bucket samples, and the on-demand report download (derived
 * server-side from the same redacted buckets — never stored).
 */
export function ComparisonResultDetail({ runId, result }: { runId: string; result: Result }) {
  const observed = (result.observed_value ?? {}) as Record<string, unknown>;
  const sample = (result.sample_failures ?? {}) as Record<string, unknown>;
  const { message } = App.useApp();
  const [downloading, setDownloading] = useState<string>();

  const download = async (fmt: 'csv' | 'xlsx') => {
    setDownloading(fmt);
    try {
      await downloadComparisonReport(runId, result.id, fmt);
    } catch {
      message.error('Report download failed');
    } finally {
      setDownloading(undefined);
    }
  };

  return (
    <Flex vertical gap={16} data-testid="comparison-result-detail">
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Text strong>Reconciliation</Typography.Text>
        <Flex gap={8}>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={downloading === 'csv'}
            onClick={() => void download('csv')}
          >
            CSV report
          </Button>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={downloading === 'xlsx'}
            onClick={() => void download('xlsx')}
          >
            XLSX report
          </Button>
        </Flex>
      </Flex>
      <Descriptions
        size="small"
        column={{ xs: 2, sm: 4 }}
        items={COUNT_LABELS.filter(([key]) => observed[key] !== undefined).map(([key, label]) => ({
          key,
          label,
          children: String(observed[key]),
        }))}
      />
      {isRecord(observed.per_column) && (
        <div data-testid="comparison-per-column">
          <Typography.Text type="secondary">Per-column breakdown</Typography.Text>
          <Table
            size="small"
            rowKey="column"
            pagination={false}
            scroll={{ x: 'max-content' }}
            columns={[
              { title: 'Column', dataIndex: 'column' },
              ...PER_COLUMN_BUCKETS.map(([bucket, label]) => ({
                title: label,
                dataIndex: bucket,
                render: (v: unknown) => String(v ?? 0),
              })),
            ]}
            dataSource={Object.entries(observed.per_column as Record<string, unknown>).map(
              ([column, counts]) => ({
                column,
                ...(isRecord(counts) ? counts : {}),
              }),
            )}
          />
        </div>
      )}
      {BUCKETS.map(({ key, label }) => {
        const rows = sample[key];
        if (!Array.isArray(rows) || rows.length === 0) return null;
        const columns = [...new Set(rows.flatMap((r) => Object.keys(r as object)))];
        return (
          <div key={key}>
            <Typography.Text type="secondary">{label} (sample)</Typography.Text>
            <Table
              size="small"
              rowKey={(_, i) => `${key}-${i}`}
              pagination={false}
              scroll={{ x: 'max-content' }}
              columns={columns.map((c) => ({
                title: c,
                dataIndex: c,
                render: (v: unknown) => (v === null || v === undefined ? '—' : String(v)),
              }))}
              dataSource={rows as Record<string, unknown>[]}
            />
          </div>
        );
      })}
    </Flex>
  );
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
