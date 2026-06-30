import { LoadingOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Flex,
  Input,
  Select,
  Table,
  Typography,
} from 'antd';
import { useState } from 'react';

import {
  type ColumnProfile,
  type ColumnProfileRequest,
  listColumns,
  profileColumns,
  type ProfileResult,
  targetString,
} from '../../api/suites';
import { formatScalar } from '../results/resultsFormat';

/**
 * Inline column-profiler panel for the check editor: profiles one column of the
 * suite's run target (#215) — nulls, distinct count, min/max, top values — so
 * the author can ground a check's config (a value range, an allowed set, a null
 * threshold) in the real data before saving. Reads-only, persists nothing
 * (`POST /suites/{id}/profile`). Shared by the create page (`CheckNew`) and the
 * edit page (`CheckEdit`).
 *
 * The profiled column pre-fills from the check's `column` config field (so
 * picking a column for the expectation primes the profiler), but stays editable
 * so the author can explore neighbouring columns. Collapsed by default to keep
 * the editor uncluttered — it's an opt-in aid, not always-on.
 *
 * The column input is a **searchable dropdown** of the target's actual columns
 * (#474) — introspected lazily when the panel first opens (the lookup hits the
 * warehouse, so not on mount). If introspection fails or returns nothing (no
 * credential, unreachable warehouse), it degrades to a free-text input so the
 * profiler is never *blocked* on introspection.
 *
 * Backend limits surface as the API's error message (e.g. no credential,
 * unreachable warehouse); the button is disabled with a reason until the suite
 * has a table/file target and a column is entered.
 */
export function ColumnProfilePanel({
  suiteId,
  target,
  column,
}: {
  suiteId: string;
  /** The suite's run target (#215) — supplies the table/file identity. */
  target: Record<string, unknown> | null;
  /** The check's currently-selected column, used to pre-fill the input. */
  column: string | undefined;
}) {
  const profileTarget = extractProfileTarget(target);

  // Pre-fill from the check's column, but let the author override. Render-phase
  // sync (React's "adjust state when a prop changes" pattern) so picking the
  // expectation's column primes the input without an effect round-trip.
  const [value, setValue] = useState(column ?? '');
  const [prevColumn, setPrevColumn] = useState(column);
  if (column !== prevColumn) {
    setPrevColumn(column);
    if (column) setValue(column);
  }

  const [state, setState] = useState<
    | { status: 'idle' }
    | { status: 'running' }
    | { status: 'ok'; result: ProfileResult }
    | { status: 'error'; error: string }
  >({ status: 'idle' });

  // Column introspection for the dropdown (#474). Fetched once, lazily, the first
  // time the panel is expanded (event-driven — the lookup is a live warehouse
  // round-trip and the panel is collapsed by default). A suite's target is fixed
  // while authoring its checks, so a fetch-on-open is sufficient.
  const [cols, setCols] = useState<
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'loaded'; columns: string[] }
    | { status: 'error' }
  >({ status: 'idle' });
  const loadColumns = () => {
    if (cols.status !== 'idle' || !profileTarget) return; // fetch once
    setCols({ status: 'loading' });
    listColumns(suiteId, profileTarget)
      .then((columns) => setCols({ status: 'loaded', columns }))
      .catch(() => setCols({ status: 'error' }));
  };

  // A real dropdown only when introspection returned columns; otherwise the
  // free-text input. The current value is always offered (even if not in the
  // introspected set) so a pre-filled expectation column still shows.
  const useDropdown = cols.status === 'loaded' && cols.columns.length > 0;
  const options = (() => {
    if (cols.status !== 'loaded') return [];
    const names = value && !cols.columns.includes(value) ? [value, ...cols.columns] : cols.columns;
    return names.map((c) => ({ label: c, value: c }));
  })();

  const disabledReason = !profileTarget
    ? 'Set a table or file target on the suite to profile.'
    : !value.trim()
      ? 'Enter a column to profile.'
      : undefined;

  const run = async () => {
    if (!profileTarget || !value.trim()) return;
    setState({ status: 'running' });
    try {
      const result = await profileColumns(suiteId, {
        columns: [value.trim()],
        ...profileTarget,
      });
      setState({ status: 'ok', result });
    } catch (err) {
      setState({ status: 'error', error: err instanceof Error ? err.message : 'unknown error' });
    }
  };

  return (
    <Collapse
      size="small"
      onChange={(keys) => {
        // Introspect columns the first time the profiler is expanded.
        if ((Array.isArray(keys) ? keys : [keys]).includes('profiler')) loadColumns();
      }}
      items={[
        {
          key: 'profiler',
          label: 'Column profiler',
          children: (
            <Flex vertical gap={8}>
              <Flex gap={8} align="center" wrap>
                {useDropdown ? (
                  <Select
                    showSearch
                    value={value || undefined}
                    onChange={(v) => setValue(v ?? '')}
                    options={options}
                    optionFilterProp="label"
                    placeholder="Select a column"
                    style={{ minWidth: 240 }}
                  />
                ) : (
                  <Input
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    placeholder="Column to profile"
                    style={{ maxWidth: 240 }}
                    onPressEnter={run}
                    suffix={cols.status === 'loading' ? <LoadingOutlined /> : undefined}
                  />
                )}
                <Button
                  onClick={run}
                  loading={state.status === 'running'}
                  disabled={!!disabledReason}
                >
                  Profile
                </Button>
                {disabledReason && (
                  <Typography.Text type="secondary">{disabledReason}</Typography.Text>
                )}
              </Flex>
              {state.status === 'ok' && <ProfileView result={state.result} />}
              {state.status === 'error' && (
                <Alert type="error" showIcon message="Profile failed" description={state.error} />
              )}
            </Flex>
          ),
        },
      ]}
    />
  );
}

/** Pull the datasource-shaped identity out of the suite target, or null when no
 *  profilable target is set (the backend needs a `table` or a `path`). */
function extractProfileTarget(
  target: Record<string, unknown> | null,
): Pick<ColumnProfileRequest, 'table' | 'schema' | 'catalog' | 'path' | 'file_format'> | null {
  const table = targetString(target, 'table');
  const path = targetString(target, 'path');
  if (!table && !path) return null;
  return {
    table,
    schema: targetString(target, 'schema'),
    catalog: targetString(target, 'catalog'),
    path,
    file_format: targetString(target, 'file_format') as 'csv' | 'parquet' | undefined,
  };
}

function ProfileView({ result }: { result: ProfileResult }) {
  const col: ColumnProfile | undefined = result.columns[0];
  if (!col) return null;
  const nullPct = (col.null_fraction * 100).toFixed(1);
  return (
    <Flex vertical gap={8}>
      <Descriptions
        size="small"
        bordered
        column={1}
        styles={{ label: { width: 140 } }}
        items={[
          { key: 'column', label: 'Column', children: col.column },
          { key: 'rows', label: 'Row count', children: result.row_count },
          {
            key: 'nulls',
            label: 'Nulls',
            children: `${col.null_count} (${nullPct}%)`,
          },
          {
            key: 'distinct',
            label: 'Distinct',
            children: col.distinct_count === null ? '—' : col.distinct_count,
          },
          { key: 'min', label: 'Min', children: formatScalar(col.min_value) },
          { key: 'max', label: 'Max', children: formatScalar(col.max_value) },
        ]}
      />
      {col.top_values.length > 0 && (
        <Table
          size="small"
          pagination={false}
          rowKey={(_, i) => String(i)}
          dataSource={col.top_values}
          columns={[
            { title: 'Top value', dataIndex: 'value', render: (v: unknown) => formatScalar(v) },
            { title: 'Count', dataIndex: 'count', width: 100 },
          ]}
        />
      )}
    </Flex>
  );
}
