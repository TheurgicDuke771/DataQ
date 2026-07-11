import { ApartmentOutlined, ArrowLeftOutlined, EditOutlined } from '@ant-design/icons';
import { App, Button, Card, Empty, Flex, Input, Modal, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  type AssetDetail as AssetDetailData,
  type ComposingSuite,
  type LineageNode,
  getAsset,
  updateAsset,
} from '../api/assets';
import { useIsWorkspaceAdmin } from '../auth/useMe';
import { AssetHealthTag } from '../components/assets/AssetHealthTag';
import { runHealth } from '../components/assets/health';
import { AsyncBody } from '../components/AsyncBody';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import SimpleList from '../components/SimpleList';
import { useAsyncData } from '../hooks/useAsyncData';
import { errorMessage } from '../utils/errors';

/**
 * Asset detail (`/assets/:assetId`, #760) — identity header, health across the
 * composing suites (the acceptance criterion: renders ≥2 suites on a shared
 * asset), and upstream/downstream lineage lists. Links out to each suite and its
 * latest run. Read-only apart from the workspace-Admin-only description edit
 * (ADR 0034 §4); no navigation inversion (phase 4).
 */
export function AssetDetail() {
  const navigate = useNavigate();
  const { assetId } = useParams<{ assetId: string }>();
  const { state, reload } = useAsyncData(() => {
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
          <AssetDetailBody
            asset={asset}
            onOpenRun={(id) => navigate(`/results/${id}`)}
            onChanged={reload}
          />
        )}
      </AsyncBody>
    </Page>
  );
}

function AssetDetailBody({
  asset,
  onOpenRun,
  onChanged,
}: {
  asset: AssetDetailData;
  onOpenRun: (runId: string) => void;
  onChanged: () => void;
}) {
  const { summary } = asset;
  const navigate = useNavigate();
  // Asset-metadata mutation is workspace-Admin-only (ADR 0034 §4; backend 403s
  // everyone else) — the edit affordance renders only for admins. This gate is
  // nav convenience, not the security boundary (that's the PATCH's 403).
  const isAdmin = useIsWorkspaceAdmin();
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

      <DescriptionBlock
        assetId={summary.id}
        description={summary.description}
        canEdit={isAdmin}
        onChanged={onChanged}
      />

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

/**
 * The asset description + the workspace-Admin-only inline edit (#760). Owner
 * reassignment (`owner_user_id`) is deliberately NOT surfaced yet — it needs a
 * user picker and only matters once incident routing consumes asset owners
 * (ADR 0034 §3); the API already supports it for when that lands.
 */
function DescriptionBlock({
  assetId,
  description,
  canEdit,
  onChanged,
}: {
  assetId: string;
  description: string | null;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const openEditor = () => {
    setDraft(description ?? '');
    setEditing(true);
  };

  const onSave = async () => {
    setSaving(true);
    try {
      // Empty draft clears the description (explicit null — the PATCH's
      // omitted-vs-null semantics make that an intentional unset).
      await updateAsset(assetId, { description: draft.trim() || null });
      message.success('Description updated');
      setEditing(false);
      onChanged();
    } catch (err) {
      message.error(`Update failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  if (!description && !canEdit) return null;
  return (
    <>
      <Flex gap={8} align="baseline" wrap>
        {description ? (
          <Typography.Paragraph style={{ margin: 0 }}>{description}</Typography.Paragraph>
        ) : (
          <Typography.Text type="secondary">No description yet.</Typography.Text>
        )}
        {canEdit && (
          <Button type="link" size="small" icon={<EditOutlined />} onClick={openEditor}>
            Edit
          </Button>
        )}
      </Flex>
      <Modal
        title="Edit asset description"
        open={editing}
        onOk={() => void onSave()}
        okText="Save"
        confirmLoading={saving}
        onCancel={() => setEditing(false)}
        destroyOnHidden
      >
        <Input.TextArea
          rows={3}
          maxLength={1024}
          showCount
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="What is this asset, and who should care when it breaks?"
        />
      </Modal>
    </>
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
        // SimpleList (the #516 antd-List shim) — antd's List is deprecated in v6.
        <SimpleList<LineageNode>
          size="small"
          dataSource={nodes}
          rowKey="id"
          renderItem={(node) => (
            <SimpleList.Item>
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
            </SimpleList.Item>
          )}
        />
      )}
    </Card>
  );
}
