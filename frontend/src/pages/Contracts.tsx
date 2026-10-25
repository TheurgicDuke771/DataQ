import { UploadOutlined } from '@ant-design/icons';
import { App, Button, Flex, Form, Input, Select, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { type Connection, DATASOURCE_TYPES, listConnections } from '../api/connections';
import { type Contract, importContract, listContracts } from '../api/contracts';
import { useCanAuthor } from '../auth/useMe';
import { AsyncBody } from '../components/AsyncBody';
import { Modal } from '../components/shared/Modal';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { EmptyState } from '../components/shared/EmptyState';
import { RowLink } from '../components/shared/RowLink';
import { useAsyncData } from '../hooks/useAsyncData';
import { errorMessage } from '../utils/errors';

/**
 * Data contracts (`/contracts`, ADR 0052): the ODCS contracts DataQ enforces, one row per
 * imported version. Read-only apart from importing a document — contracts are authored where the
 * producer keeps them.
 */
export function Contracts() {
  const { state, reload } = useAsyncData((signal) => listContracts(signal));
  const canImport = useCanAuthor();
  const [importOpen, setImportOpen] = useState(false);

  return (
    <Page>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Data contracts
        </Typography.Title>
        {canImport && (
          <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
            Import contract
          </Button>
        )}
      </Flex>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        Open Data Contract Standard documents and the suites that enforce them. A clause DataQ
        cannot check is listed as not enforced, never dropped.
      </Typography.Paragraph>
      <AsyncBody state={state} errorTitle="Could not load contracts" onRetry={reload}>
        {(contracts) =>
          contracts.length === 0 ? (
            <EmptyState
              title="No contracts yet"
              description="Import an ODCS v3 document to create the suites and checks it describes."
              action={
                canImport
                  ? { label: 'Import contract', onClick: () => setImportOpen(true) }
                  : undefined
              }
            />
          ) : (
            <ContractsTable contracts={contracts} />
          )
        }
      </AsyncBody>
      <ImportContractModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={reload}
      />
    </Page>
  );
}

function ContractsTable({ contracts }: { contracts: Contract[] }) {
  const columns: ColumnsType<Contract> = [
    {
      title: 'Contract',
      key: 'name',
      render: (_, c) => <RowLink to={`/contracts/${c.id}`}>{c.name}</RowLink>,
    },
    {
      title: 'Version',
      key: 'version',
      render: (_, c) => (
        <Flex gap={6} align="center">
          <Typography.Text>{c.version}</Typography.Text>
          {c.latest_version ? <Tag color="blue">Latest</Tag> : <Tag>Superseded</Tag>}
        </Flex>
      ),
    },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (s: string | null) => s ?? '—' },
    { title: 'Domain', dataIndex: 'domain', key: 'domain', render: (d: string | null) => d ?? '—' },
    {
      title: 'Suites',
      key: 'suites',
      render: (_, c) =>
        c.restricted_suite_count > 0
          ? `${c.suites.length} (+${c.restricted_suite_count} you cannot see)`
          : String(c.suites.length),
    },
    {
      title: 'Not enforced',
      key: 'unmapped',
      render: (_, c) => (c.unmapped.length > 0 ? `${c.unmapped.length} clauses` : 'None'),
    },
    {
      title: 'Imported',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (at: string) => formatTimestamp(at),
    },
  ];
  return (
    <Table<Contract>
      rowKey="id"
      columns={columns}
      dataSource={contracts}
      pagination={false}
      aria-label="Data contracts"
    />
  );
}

function ImportContractModal({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  return (
    <Modal
      open={open}
      title="Import a data contract"
      footer={null}
      onCancel={onClose}
      destroyOnHidden
    >
      {open && <ImportContractForm onClose={onClose} onImported={onImported} />}
    </Modal>
  );
}

function ImportContractForm({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<{ connection_id: string; document_yaml: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Mounted only while the modal is open, so the connections are fetched when it opens.
  const connections = useAsyncData<Connection[]>(() => listConnections());
  const datasources =
    connections.state.status === 'ok'
      ? connections.state.data.filter((c) =>
          (DATASOURCE_TYPES as readonly string[]).includes(c.type),
        )
      : [];

  const submit = async (values: { connection_id: string; document_yaml: string }) => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await importContract(values);
      message.success(
        `Imported ${result.contract.name} ${result.contract.version}: ${result.created_suite_ids.length} suites, ${result.checks_added} checks`,
      );
      onImported();
      onClose();
      navigate(`/contracts/${result.contract.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Form form={form} layout="vertical" onFinish={submit}>
      <Form.Item
        name="connection_id"
        label="Connection the data lives on"
        rules={[{ required: true, message: 'Choose a connection' }]}
      >
        <Select
          loading={connections.state.status === 'loading'}
          options={datasources.map((c) => ({
            value: c.id,
            label: `${c.name} (${c.type}, ${c.env})`,
          }))}
        />
      </Form.Item>
      <Form.Item
        name="document_yaml"
        label="ODCS document (YAML)"
        extra="Paste the document, or choose a file below."
        rules={[{ required: true, message: 'Paste or choose an ODCS document' }]}
      >
        <Input.TextArea rows={10} spellCheck={false} />
      </Form.Item>
      <Form.Item label="ODCS file">
        <input
          type="file"
          accept=".yaml,.yml,.json"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void file.text().then((text) => form.setFieldValue('document_yaml', text));
          }}
        />
      </Form.Item>
      {error && (
        <Typography.Paragraph type="danger" role="alert">
          {error}
        </Typography.Paragraph>
      )}
      <Flex justify="end" gap={8}>
        <Button onClick={onClose}>Cancel</Button>
        <Button type="primary" htmlType="submit" loading={submitting}>
          Import
        </Button>
      </Flex>
    </Form>
  );
}
