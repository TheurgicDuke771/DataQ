import { Button, Flex, Space, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import { type AdminSuite, listAdminSuites } from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncData } from '../../hooks/useAsyncData';
import { DataTable, Identity, Section } from './parts';
import { SuiteAdminDeleteModal } from './SuiteAdminDeleteModal';
import { SuiteTransferModal } from './SuiteTransferModal';

/** Every suite in the workspace, unscoped by the ADR-0027 grant ladder. */
export function AdminSuites() {
  const suites = useAsyncData(listAdminSuites);
  const [transferring, setTransferring] = useState<AdminSuite | null>(null);
  const [deleting, setDeleting] = useState<AdminSuite | null>(null);
  return (
    <Section title="All suites">
      <DataTable
        state={suites.state}
        columns={suiteColumns(setTransferring, setDeleting)}
        rowKey={(s) => s.id}
        errorMessage="Failed to load suites"
      />
      <SuiteTransferModal
        key={`transfer-${transferring?.id ?? 'none'}`}
        suite={transferring}
        onClose={() => setTransferring(null)}
        onTransferred={suites.reload}
      />
      <SuiteAdminDeleteModal
        key={`delete-${deleting?.id ?? 'none'}`}
        suite={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={suites.reload}
      />
    </Section>
  );
}

/** A factory, not a constant: the action cell needs the two modal openers. */
const suiteColumns = (
  onTransfer: (s: AdminSuite) => void,
  onDelete: (s: AdminSuite) => void,
): ColumnsType<AdminSuite> => [
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
  {
    title: 'Actions',
    key: 'actions',
    render: (_, s) => (
      <Space size={4}>
        <Button size="small" onClick={() => onTransfer(s)}>
          Transfer
        </Button>
        <Button size="small" danger type="text" onClick={() => onDelete(s)}>
          Delete
        </Button>
      </Space>
    ),
  },
];
