import {
  CloudOutlined,
  DatabaseOutlined,
  FileOutlined,
  GoldOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Empty, Flex, Segmented, Table, Tag, Tooltip, Tree, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { DataNode } from 'antd/es/tree';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { type AssetSummary, listAssets } from '../api/assets';
import { namespaceLabel } from '../components/assets/namespaceLabel';
import { AssetHealthTag } from '../components/assets/AssetHealthTag';
import {
  type AssetTreeNode,
  type DatasourceKind,
  buildAssetTree,
  expandableKeys,
} from '../components/assets/assetTree';
import { AsyncBody } from '../components/AsyncBody';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Assets list (`/assets`, ADR 0034 gap G-d phase 2, #760; hierarchical browse
 * #802) — the read-only browse/reason surface over data assets. Every member
 * sees every asset (ADR 0037 — identity is workspace knowledge), with health
 * rolled up workspace-true across ALL composing suites.
 *
 * Two lenses over the same data:
 * - **By source** (default) — a connection-rooted drill-down
 *   (namespace → database/catalog → schema → table); the leaves open the detail.
 * - **All assets** — the flat, searchable table (retained per #802).
 */
export function Assets() {
  const navigate = useNavigate();
  const { state } = useAsyncData(listAssets);
  const [view, setView] = useState<'tree' | 'table'>('tree');
  const onOpen = (id: string) => navigate(`/assets/${id}`);

  return (
    <Page>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Assets
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        The tables and files DataQ knows about. Health is rolled up across every suite that targets
        the asset.
      </Typography.Paragraph>
      <AsyncBody state={state} loadingText="Loading assets…" errorTitle="Failed to load assets">
        {(assets) =>
          assets.length === 0 ? (
            <Empty description="No assets yet — give a suite a run target and it will appear here." />
          ) : (
            <Flex vertical gap={16} align="stretch">
              <Segmented<'tree' | 'table'>
                value={view}
                onChange={setView}
                style={{ alignSelf: 'flex-start' }}
                options={[
                  { label: 'By source', value: 'tree' },
                  { label: 'All assets', value: 'table' },
                ]}
              />
              {view === 'tree' ? (
                <AssetsTree assets={assets} onOpen={onOpen} />
              ) : (
                <AssetsTable assets={assets} onOpen={onOpen} />
              )}
            </Flex>
          )
        }
      </AsyncBody>
    </Page>
  );
}

const KIND_ICON: Record<DatasourceKind, ReactNode> = {
  snowflake: <DatabaseOutlined />,
  unity_catalog: <ThunderboltOutlined />,
  adls_gen2: <CloudOutlined />,
  s3: <CloudOutlined />,
  iceberg: <GoldOutlined />,
  other: <FileOutlined />,
};

/**
 * Connection-rooted drill-down over the assets (#802). The tree is derived purely
 * from each asset's OL namespace + name (`buildAssetTree`); selecting a leaf (a
 * node carrying an `asset`) opens its detail. Folder nodes just expand. Env stays
 * visible as a per-leaf tag so DEV/QA assets read as distinct (ADR 0034).
 */
function AssetsTree({ assets, onOpen }: { assets: AssetSummary[]; onOpen: (id: string) => void }) {
  const tree = useMemo(() => buildAssetTree(assets), [assets]);
  const treeData = useMemo(() => tree.map(toDataNode), [tree]);
  // Map node key → asset id so a leaf select navigates; folders aren't in the map.
  const idByKey = useMemo(() => {
    const map = new Map<string, string>();
    const walk = (nodes: AssetTreeNode[]) => {
      for (const n of nodes) {
        if (n.asset) map.set(n.key, n.asset.id);
        walk(n.children);
      }
    };
    walk(tree);
    return map;
  }, [tree]);
  // Expand the datasource + folder levels by default so the drill-down is visible
  // without a click; leaves stay one expand away.
  const defaultExpandedKeys = useMemo(() => expandableKeys(tree), [tree]);

  return (
    <Tree
      showLine
      showIcon
      defaultExpandedKeys={defaultExpandedKeys}
      treeData={treeData}
      selectedKeys={[]}
      onSelect={(keys) => {
        const id = keys.length > 0 ? idByKey.get(String(keys[0])) : undefined;
        if (id) onOpen(id);
      }}
    />
  );
}

/** Map a pure `AssetTreeNode` to an antd `DataNode` (icons, env tag, health). */
function toDataNode(node: AssetTreeNode): DataNode {
  const icon = node.kind ? KIND_ICON[node.kind] : undefined;
  const title = node.asset ? (
    <Flex align="center" gap={8} style={{ minWidth: 0 }}>
      <span>{node.label}</span>
      {node.asset.env && <Tag style={{ marginInlineEnd: 0 }}>{node.asset.env}</Tag>}
      <AssetHealthTag summary={node.asset} />
    </Flex>
  ) : node.namespace ? (
    // A datasource root: show the human label, keep the raw OL namespace (the
    // identity) one hover away rather than printing a DSN at people (#830).
    <Tooltip title={node.namespace}>
      <span>{node.label}</span>
    </Tooltip>
  ) : (
    <span>{node.label}</span>
  );
  return {
    key: node.key,
    title,
    icon,
    // A folder-and-leaf node keeps its children; a pure leaf has none.
    children: node.children.length > 0 ? node.children.map(toDataNode) : undefined,
    isLeaf: node.children.length === 0,
  };
}

function AssetsTable({ assets, onOpen }: { assets: AssetSummary[]; onOpen: (id: string) => void }) {
  const columns: ColumnsType<AssetSummary> = [
    {
      title: 'Asset',
      dataIndex: 'name',
      render: (name: string, asset) => (
        <div style={{ minWidth: 0 }}>
          <Typography.Text strong ellipsis style={{ display: 'block' }}>
            {name}
          </Typography.Text>
          <Tooltip title={asset.namespace}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
              {namespaceLabel(asset.namespace)}
            </Typography.Text>
          </Tooltip>
        </div>
      ),
    },
    {
      title: 'Env',
      dataIndex: 'env',
      width: 90,
      render: (env: string | null) =>
        env ? <Tag>{env}</Tag> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: 'Suites',
      dataIndex: 'suite_count',
      width: 90,
      align: 'center',
    },
    {
      title: 'Health',
      key: 'health',
      width: 130,
      render: (_: unknown, asset) => <AssetHealthTag summary={asset} />,
    },
    {
      title: 'Last seen',
      dataIndex: 'last_seen',
      width: 200,
      render: (ts: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatTimestamp(ts)}
        </Typography.Text>
      ),
    },
  ];
  return (
    <Table<AssetSummary>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="middle"
      columns={columns}
      dataSource={assets}
      pagination={false}
      onRow={(asset) => ({
        onClick: () => onOpen(asset.id),
        style: { cursor: 'pointer' },
      })}
    />
  );
}
