import { DownloadOutlined } from '@ant-design/icons';
import { Alert, App, Button, Card, Flex, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect } from 'react';
import { useParams } from 'react-router-dom';

import {
  type ClauseState,
  type ClauseStateRow,
  type ContractDetail as ContractDetailData,
  type UnmappedClause,
  exportContractYaml,
  getContract,
} from '../api/contracts';
import { AsyncBody } from '../components/AsyncBody';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { RowLink } from '../components/shared/RowLink';
import { useAsyncData } from '../hooks/useAsyncData';
import { downloadText, toFilenameStem } from '../utils/download';
import { errorMessage } from '../utils/errors';

const STATE_LABEL: Record<ClauseState, { label: string; color?: string; help: string }> = {
  pass: { label: 'Pass', color: 'green', help: 'The check passed in the latest run.' },
  warn: { label: 'Warn', color: 'gold', help: 'The check breached its warn threshold.' },
  fail: { label: 'Fail', color: 'red', help: 'The check failed in the latest run.' },
  critical: {
    label: 'Critical',
    color: 'magenta',
    help: 'The check breached its critical threshold.',
  },
  unverified: {
    label: 'Unverified',
    help: 'The check errored or was skipped, so the clause was not verified.',
  },
  not_run: {
    label: 'Not run',
    help: 'The latest run has no result for this check: it was added since, or the run is still going or stopped early.',
  },
  disabled: {
    label: 'Switched off',
    help: 'The check is switched off, so the clause is not checked.',
  },
  missing: {
    label: 'No check',
    color: 'volcano',
    help: 'The contract needs a check here and the suite has none (deleted, or the suite was linked by hand). The clause is not enforced.',
  },
};

function clauseLabel(c: ClauseStateRow['clause']): string {
  const where = [c.object, c.property].filter(Boolean).join('.');
  return [where, c.rule_id ?? c.field].filter(Boolean).join(' · ');
}

/** One data contract (`/contracts/:contractId`, ADR 0052): per-clause compliance and what is not enforced. */
export function ContractDetail() {
  const { contractId = '' } = useParams();
  // Keyed on the id: the fetch runs on mount only, so moving to another contract in place (back /
  // forward, the command palette) must remount rather than keep the previous contract on screen.
  return <ContractDetailFor key={contractId} contractId={contractId} />;
}

function ContractDetailFor({ contractId }: { contractId: string }) {
  const { state, reload } = useAsyncData((signal) => getContract(contractId, signal));

  useEffect(() => {
    if (state.status === 'ok')
      document.title = `${state.data.name} ${state.data.version} · Contract · DataQ`;
  }, [state]);

  return (
    <Page>
      <AsyncBody state={state} errorTitle="Could not load the contract" page onRetry={reload}>
        {(contract) => <ContractBody contract={contract} />}
      </AsyncBody>
    </Page>
  );
}

function ContractBody({ contract }: { contract: ContractDetailData }) {
  const { message } = App.useApp();
  const { compliance } = contract;
  const suitesById = new Map(contract.suites.map((s) => [s.id, s]));
  const suiteName = (id: string) => suitesById.get(id)?.name ?? 'Suite';
  const partial = compliance.suites.filter((s) => !s.results_final);
  const unenforced =
    compliance.counts.missing + compliance.counts.disabled + compliance.not_enforced;

  const download = async () => {
    try {
      const yaml = await exportContractYaml(contract.id);
      downloadText(
        `${toFilenameStem(contract.name, 'contract')}_${toFilenameStem(contract.version, 'v')}.odcs.yaml`,
        yaml,
        'application/yaml',
      );
    } catch (err) {
      message.error(`Export failed: ${errorMessage(err)}`);
    }
  };

  const clauseColumns: ColumnsType<ClauseStateRow & { key: string }> = [
    { title: 'Clause', key: 'clause', render: (_, c) => clauseLabel(c.clause) },
    {
      title: 'Suite',
      key: 'suite',
      render: (_, c) => <RowLink to={`/suites/${c.suite_id}`}>{suiteName(c.suite_id)}</RowLink>,
    },
    { title: 'Check', key: 'check', render: (_, c) => c.check_name ?? '—' },
    {
      title: 'State',
      key: 'state',
      render: (_, c) => {
        const meta = STATE_LABEL[c.state];
        return (
          <Flex gap={4} align="center" wrap>
            <Tooltip title={meta.help}>
              <Tag color={meta.color} aria-label={`${meta.label}: ${meta.help}`}>
                {meta.label}
              </Tag>
            </Tooltip>
            {c.drifted && (
              <Tooltip title="The check was edited since the import, so it may no longer say what the clause says.">
                <Tag aria-label="Edited since import: may no longer match the clause">Edited</Tag>
              </Tooltip>
            )}
          </Flex>
        );
      },
    },
  ];

  const unmappedColumns: ColumnsType<UnmappedClause & { key: string }> = [
    {
      title: 'Clause',
      key: 'clause',
      render: (_, u) => clauseLabel(u),
    },
    { title: 'Why it is not enforced', dataIndex: 'reason', key: 'reason' },
  ];

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <Flex vertical gap={4}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {contract.name}{' '}
            <Typography.Text type="secondary" style={{ fontSize: 16 }}>
              {contract.version}
            </Typography.Text>
          </Typography.Title>
          <Flex gap={6} wrap>
            {contract.latest_version ? <Tag color="blue">Latest</Tag> : <Tag>Superseded</Tag>}
            {contract.status && <Tag>{contract.status}</Tag>}
            <Typography.Text type="secondary">
              ODCS {contract.api_version} · id {contract.odcs_id} · imported{' '}
              {formatTimestamp(contract.created_at)}
            </Typography.Text>
          </Flex>
        </Flex>
        <Button icon={<DownloadOutlined />} onClick={download}>
          Download ODCS
        </Button>
      </Flex>

      {contract.restricted_suite_count > 0 && (
        <Alert
          type="info"
          showIcon
          title={`${contract.restricted_suite_count} linked suites are not shown`}
          description="You cannot see them, so their clauses are left out of this page entirely."
        />
      )}
      {partial.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="Not a final verdict yet"
          description={`${partial.length} suites have no completed latest run (still running, stopped early, or never run), so their clause states are partial.`}
        />
      )}

      <Flex gap={12} wrap role="list" aria-label="Clause counts">
        {(
          [
            'pass',
            'warn',
            'fail',
            'critical',
            'unverified',
            'not_run',
            'disabled',
            'missing',
          ] as const
        ).map((key) => (
          <Card key={key} size="small" role="listitem">
            <Typography.Text type="secondary">{STATE_LABEL[key].label}</Typography.Text>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {compliance.counts[key] ?? 0}
            </Typography.Title>
          </Card>
        ))}
        <Card size="small" role="listitem">
          <Typography.Text type="secondary">Not enforced</Typography.Text>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {compliance.not_enforced}
          </Typography.Title>
        </Card>
      </Flex>
      {unenforced > 0 && (
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          {unenforced} clauses are not being checked at all (no check, switched off, or not
          enforceable by DataQ).
        </Typography.Paragraph>
      )}

      <Card title="Suites" size="small">
        <Table
          rowKey="suite_id"
          size="small"
          pagination={false}
          aria-label="Suites that enforce this contract"
          dataSource={compliance.suites}
          columns={[
            {
              title: 'Suite',
              key: 'suite',
              render: (_, s) => (
                <RowLink to={`/suites/${s.suite_id}`}>{suiteName(s.suite_id)}</RowLink>
              ),
            },
            {
              title: 'Object',
              key: 'object',
              render: (_, s) => suitesById.get(s.suite_id)?.contract_object ?? '—',
            },
            {
              title: 'Latest run',
              key: 'run',
              render: (_, s) =>
                s.last_run_id ? (
                  <RowLink to={`/results/${s.last_run_id}`}>
                    {s.last_run_status} · {formatTimestamp(s.last_run_at)}
                  </RowLink>
                ) : (
                  'Never run'
                ),
            },
            {
              title: 'Final',
              key: 'final',
              render: (_, s) => (s.results_final ? 'Yes' : 'No — partial'),
            },
          ]}
        />
      </Card>

      <Card title="Clauses" size="small">
        <Table<ClauseStateRow & { key: string }>
          rowKey="key"
          size="small"
          pagination={false}
          aria-label="Contract clauses and their state"
          dataSource={compliance.clauses.map((c, i) => ({ ...c, key: `${c.suite_id}-${i}` }))}
          columns={clauseColumns}
        />
      </Card>

      <Card title="Not enforced" size="small">
        {contract.unmapped.length === 0 ? (
          <Typography.Text type="secondary">Every clause maps to a check.</Typography.Text>
        ) : (
          <Table<UnmappedClause & { key: string }>
            rowKey="key"
            size="small"
            pagination={false}
            aria-label="Clauses DataQ does not enforce"
            dataSource={contract.unmapped.map((u, i) => ({ ...u, key: String(i) }))}
            columns={unmappedColumns}
          />
        )}
      </Card>
    </Flex>
  );
}
