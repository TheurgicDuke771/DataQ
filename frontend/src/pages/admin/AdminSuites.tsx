import { Flex, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { type AdminSuite, listAdminSuites } from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncData } from '../../hooks/useAsyncData';
import { DataTable, Identity, Section } from './parts';

/** Every suite in the workspace, unscoped by the ADR-0027 grant ladder. */
export function AdminSuites() {
  const suites = useAsyncData(listAdminSuites);
  return (
    <Section title="All suites">
      <DataTable
        state={suites.state}
        columns={SUITE_COLUMNS}
        rowKey={(s) => s.id}
        errorMessage="Failed to load suites"
      />
    </Section>
  );
}

const SUITE_COLUMNS: ColumnsType<AdminSuite> = [
  { title: 'Suite', dataIndex: 'name' },
  {
    title: 'Owner',
    key: 'owner',
    render: (_, s) => <Identity name={s.owner_name} email={s.owner_email} />,
  },
  {
    title: 'Datasource',
    key: 'datasource',
    render: (_, s) => (
      <Flex align="center" gap={6}>
        <Typography.Text>{s.connection_name}</Typography.Text>
        <Tag>{s.connection_type}</Tag>
      </Flex>
    ),
  },
  { title: 'Env', dataIndex: 'env', render: (env: string) => <Tag>{env}</Tag> },
  { title: 'Checks', dataIndex: 'check_count', align: 'right' },
  { title: 'Shared with', dataIndex: 'share_count', align: 'right' },
  { title: 'Created', dataIndex: 'created_at', render: (v: string) => formatTimestamp(v) },
];
