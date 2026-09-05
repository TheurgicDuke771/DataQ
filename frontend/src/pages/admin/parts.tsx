import { Alert, Card, Flex, Spin, Table, Typography } from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import type { ReactNode } from 'react';

import type { AsyncState } from '../../hooks/useAsyncData';

/** Titled card wrapper shared by every admin sub-page section. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card title={title} size="small">
      <Flex vertical gap={16}>
        {children}
      </Flex>
    </Card>
  );
}

/** Load/error/table boilerplate for an already-fetched admin dataset. */
export function DataTable<T extends object>({
  state,
  columns,
  rowKey,
  errorMessage,
  pagination,
}: {
  state: AsyncState<T[]>;
  columns: ColumnsType<T>;
  rowKey: (row: T) => string;
  errorMessage: string;
  /** Defaults to a 20-row client page. Pass a real config for server-side
   *  pagination, or `false` to turn it off. */
  pagination?: TablePaginationConfig | false;
}) {
  if (state.status === 'loading') return <Spin size="large" />;
  if (state.status === 'error') {
    // Sub-panel inside a working page → inline Alert, not the full-page error
    // the /me failure warrants (#910).
    return <Alert type="error" showIcon title={errorMessage} description={state.error} />;
  }
  return (
    <Table
      scroll={{ x: 'max-content' }}
      dataSource={state.data}
      columns={columns}
      rowKey={rowKey}
      size="small"
      pagination={pagination ?? { pageSize: 20, hideOnSinglePage: true }}
    />
  );
}

/** Name over email, falling back to the email alone when no display name. */
export function Identity({ name, email }: { name: string | null; email: string }) {
  if (!name) return <Typography.Text>{email}</Typography.Text>;
  return (
    <Flex vertical>
      <Typography.Text>{name}</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {email}
      </Typography.Text>
    </Flex>
  );
}
