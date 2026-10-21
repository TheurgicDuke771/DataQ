import { Alert, App, Button, Card, Popconfirm, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useRef } from 'react';

import { type DetectedClassification, setAssetClassificationDismissed } from '../../api/assets';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { formatTimestamp } from '../results/resultsFormat';

const DETECTOR_LABEL: Record<string, string> = {
  email: 'email addresses',
  encoded: 'encoded blobs',
};

/**
 * Columns the profiler found PII-shaped values in (ADR 0049) — the second masking reason
 * nothing else on the page explains. A detection masks the column on every read path until a
 * workspace admin dismisses it here (audited) or a suite names it as its identifier column.
 */
export function DetectedMaskingPanel({
  assetId,
  entries,
  refreshedAt,
  canDismiss,
  onChanged,
}: {
  assetId: string;
  entries?: DetectedClassification[] | null;
  refreshedAt?: string | null;
  canDismiss: boolean;
  onChanged: () => void;
}) {
  const { run, loading } = useAsyncAction('Could not update the detection');
  const { message } = App.useApp();
  const panelRef = useRef<HTMLDivElement>(null);
  // The row's button is disabled while saving and swapped (Dismiss / Restore masking) by the
  // reload, so focus is put back on that row's button once both are done, else on the panel.
  const refocus = useRef<{
    column: string;
    entries: DetectedClassification[] | null | undefined;
    saved: boolean;
  } | null>(null);
  useEffect(() => {
    const pending = refocus.current;
    if (pending === null || loading) return;
    if (pending.saved && entries === pending.entries) return;
    refocus.current = null;
    const target = [
      ...(panelRef.current?.querySelectorAll<HTMLElement>('[data-detection-action]') ?? []),
    ].find((el) => el.dataset.detectionAction === pending.column);
    (target ?? panelRef.current)?.focus();
  }, [entries, loading]);

  // Absent (an older API) says nothing, so render nothing.
  if (entries === undefined) return null;
  if (entries === null) {
    return (
      <Alert
        type="warning"
        showIcon
        data-testid="detected-masking-unknown"
        title="Could not check whether the profiler has detected personal data here: reading the detections failed."
      />
    );
  }
  // Never profiled and nothing found: there is nothing to explain yet.
  if (entries.length === 0 && !refreshedAt) return null;

  const setDismissed = (column: string, dismissed: boolean) => {
    const pending = { column, entries, saved: false };
    refocus.current = pending;
    void run(async () => {
      await setAssetClassificationDismissed(assetId, column, dismissed);
      message.success(dismissed ? `${column}: detection dismissed` : `${column}: masked again`);
      pending.saved = true;
      onChanged();
    });
  };

  const columns: ColumnsType<DetectedClassification> = [
    {
      title: 'Column',
      dataIndex: 'column',
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: 'Looked like',
      dataIndex: 'detector',
      render: (value: string) => DETECTOR_LABEL[value] ?? value,
    },
    {
      title: 'First seen',
      dataIndex: 'first_detected_at',
      render: (value: string) => formatTimestamp(value),
    },
    {
      title: 'Last seen',
      dataIndex: 'last_detected_at',
      render: (value: string) => formatTimestamp(value),
    },
    {
      title: 'Masking',
      key: 'masking',
      render: (_: unknown, row: DetectedClassification) => {
        if (row.effect === 'dismissed') {
          return (
            <Tag data-testid={`detection-${row.column}-dismissed`}>
              dismissed {formatTimestamp(row.dismissed_at)}
            </Tag>
          );
        }
        if (row.effect === 'overridden_by_tag') {
          return (
            <Tooltip
              trigger={['hover', 'focus']}
              title="This column carries its own warehouse tag, which decides instead; the detection changes nothing."
            >
              <Tag tabIndex={0} data-testid={`detection-${row.column}-overridden`}>
                warehouse tag decides
              </Tag>
            </Tooltip>
          );
        }
        return (
          <Tag color="orange" data-testid={`detection-${row.column}-masked`}>
            masked
          </Tag>
        );
      },
    },
    ...(canDismiss
      ? [
          {
            title: <span className="dq-sr-only">Actions</span>,
            key: 'actions',
            render: (_: unknown, row: DetectedClassification) =>
              row.dismissed ? (
                <Button
                  size="small"
                  disabled={loading}
                  data-detection-action={row.column}
                  aria-label={`Restore masking: ${row.column}`}
                  onClick={() => setDismissed(row.column, false)}
                >
                  Restore masking
                </Button>
              ) : (
                <Popconfirm
                  title="Dismiss this detection?"
                  description="Failing samples will show this column's values again. The dismissal is recorded in the audit log."
                  okText="Dismiss"
                  onConfirm={() => setDismissed(row.column, true)}
                >
                  <Button
                    size="small"
                    danger
                    disabled={loading}
                    data-detection-action={row.column}
                    aria-label={`Dismiss: ${row.column}`}
                  >
                    Dismiss
                  </Button>
                </Popconfirm>
              ),
          } satisfies ColumnsType<DetectedClassification>[number],
        ]
      : []),
  ];

  return (
    <Card
      ref={panelRef}
      tabIndex={-1}
      role="group"
      aria-label="Detected personal data"
      size="small"
      title="Detected personal data"
      data-testid="detected-masking-panel"
    >
      <Typography.Paragraph type="secondary">
        The profiler found values shaped like personal data in these columns, so failing samples
        mask them even though nobody classified them. A warehouse tag or a suite’s identifier column
        overrides a detection; so does a workspace admin dismissing it here. A column not listed is
        not thereby clean: only the values a profile saw were examined, and only for these two
        shapes.
        {refreshedAt && <> Last full profile {formatTimestamp(refreshedAt)}.</>}
      </Typography.Paragraph>
      {entries.length === 0 ? (
        <Typography.Text type="secondary" data-testid="detected-masking-none">
          Nothing detected in the last full profile.
        </Typography.Text>
      ) : (
        <Table
          size="small"
          rowKey="column"
          pagination={false}
          dataSource={entries}
          columns={columns}
        />
      )}
    </Card>
  );
}
