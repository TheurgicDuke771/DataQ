import { ApartmentOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Card, Empty, Flex, List, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate, useParams } from 'react-router-dom';

import {
  type AssetDetail as AssetDetailData,
  type ComposingSuite,
  type LineageNode,
  getAsset,
} from '../api/assets';
import { AssetHealthTag } from '../components/assets/AssetHealthTag';
import { runHealth } from '../components/assets/health';
import { AsyncBody } from '../components/AsyncBody';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Asset detail (`/assets/:assetId`, #760) — identity header, health across the
 * composing suites (the acceptance criterion: renders ≥2 suites on a shared
 * asset), and upstream/downstream lineage lists. Links out to each suite and its
 * latest run. Read-only; no navigation inversion (phase 4).
 */
export function AssetDetail() {
  const navigate = useNavigate();
  const { assetId } = useParams<{ assetId: string }>();
  const { state } = useAsyncData(() => {
    if (!assetId) throw new Error('no asset');
    return getAsset(assetId);
  });

  return (
    <Page gap={16}>
      <div>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/assets')}
          style={{ paddingLeft: 0 }}
        >
          Assets
        </Button>
      </div>
      <AsyncBody state={state} loadingText="Loading asset…" errorTitle="Failed to load asset">
        {(asset) => (
          <AssetDetailBody asset={asset} onOpenRun={(id) => navigate(`/results/${id}`)} />
        )}
      </AsyncBody>
    </Page>
  );
}

function AssetDetailBody({
  asset,
  onOpenRun,
}: {
  asset: AssetDetailData;
  onOpenRun: (runId: string) => void;
}) {
  const { summary } = asset;
  const navigate = useNavigate();
  return (
    <Flex vertical gap={20}>
      <Flex justify="space-between" align="flex-start" gap={12} wrap>
        <Flex vertical gap={4} style={{ minWidth: 0 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {summary.name}
          </Typography.Title>
          <Typography.Text type="secondary" copyable>
            {summary.namespace}
          </Typography.Text>
        </Flex>
        <Flex gap={8} align="center">
          {summary.env && <Tag>{summary.env}</Tag>}
          <AssetHealthTag summary={summary} />
        </Flex>
      </Flex>

      {summary.description && <Typography.Paragraph>{summary.description}</Typography.Paragraph>}

      <SuitesSection
        suites={asset.suites}
        onOpenSuite={(id) => navigate(`/suites/${id}`)}
        onOpenRun={onOpenRun}
      />

      <Flex gap={16} wrap align="stretch">
        <LineagePanel
          title="Upstream"
          nodes={asset.upstream}
          emptyHint="No known upstream sources."
        />
        <LineagePanel
          title="Downstream"
          nodes={asset.downstream}
          emptyHint="No known downstream consumers."
        />
      </Flex>
    </Flex>
  );
}

function SuitesSection({
  suites,
  onOpenSuite,
  onOpenRun,
}: {
  suites: ComposingSuite[];
  onOpenSuite: (suiteId: string) => void;
  onOpenRun: (runId: string) => void;
}) {
  const columns: ColumnsType<ComposingSuite> = [
    {
      title: 'Suite',
      dataIndex: 'name',
      render: (name: string, suite) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => onOpenSuite(suite.suite_id)}>
          {name}
        </Button>
      ),
    },
    {
      title: 'Access',
      dataIndex: 'my_permission',
      width: 100,
      render: (level: string) => <Tag>{level}</Tag>,
    },
    {
      title: 'Health',
      key: 'health',
      width: 120,
      render: (_: unknown, suite) => {
        const { label, color } = runHealth(suite.latest_run);
        return <Tag color={color}>{label}</Tag>;
      },
    },
    {
      title: 'Checks',
      key: 'checks',
      width: 90,
      align: 'center',
      render: (_: unknown, suite) => {
        const r = suite.latest_run;
        return r.checks_total === 0 ? '—' : `${r.checks_passed} / ${r.checks_total}`;
      },
    },
    {
      title: 'Last run',
      key: 'last_run',
      width: 200,
      render: (_: unknown, suite) => {
        const r = suite.latest_run;
        const ts = formatTimestamp(r.finished_at ?? r.created_at);
        if (r.run_id) {
          return (
            <Button
              type="link"
              style={{ padding: 0 }}
              onClick={() => onOpenRun(r.run_id as string)}
            >
              {ts}
            </Button>
          );
        }
        return <Typography.Text type="secondary">—</Typography.Text>;
      },
    },
  ];
  return (
    <Card
      size="small"
      title={`Monitored by ${suites.length} suite${suites.length === 1 ? '' : 's'}`}
    >
      <Table<ComposingSuite>
        scroll={{ x: 'max-content' }}
        rowKey="suite_id"
        size="small"
        columns={columns}
        dataSource={suites}
        pagination={false}
      />
    </Card>
  );
}

function LineagePanel({
  title,
  nodes,
  emptyHint,
}: {
  title: string;
  nodes: LineageNode[];
  emptyHint: string;
}) {
  return (
    <Card
      size="small"
      title={
        <Flex gap={8} align="center">
          <ApartmentOutlined />
          {title}
        </Flex>
      }
      style={{ flex: 1, minWidth: 280 }}
    >
      {nodes.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyHint} />
      ) : (
        <List<LineageNode>
          size="small"
          dataSource={nodes}
          renderItem={(node) => (
            <List.Item>
              <Flex vertical gap={2} style={{ minWidth: 0, flex: 1 }}>
                <Flex gap={8} align="center" wrap>
                  <Typography.Text strong ellipsis>
                    {node.name}
                  </Typography.Text>
                  {node.is_monitored ? <Tag color="blue">Monitored</Tag> : <Tag>Unmonitored</Tag>}
                </Flex>
                <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                  {node.namespace}
                </Typography.Text>
              </Flex>
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
