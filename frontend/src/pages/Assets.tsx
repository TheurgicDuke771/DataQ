import {
  CloudOutlined,
  DatabaseOutlined,
  FileOutlined,
  GoldOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Alert, Empty, Flex, Segmented, Table, Tag, Tooltip, Tree, Typography } from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import type { DataNode } from 'antd/es/tree';
import type { ReactNode, RefObject } from 'react';
import { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { type AssetListPage, type AssetSummary, listAssets } from '../api/assets';
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
 * Assets list (`/assets`, ADR 0034 gap G-d phase 2, #760; hierarchical browse #802; server-side
 * paging #925) — the read-only browse/reason surface over data assets.
 */
export function Assets() {
  const navigate = useNavigate();
  const [view, setView] = useState<'tree' | 'table'>('tree');
  const onOpen = (id: string) => navigate(`/assets/${id}`);
  // The completed tree walk (#1107), held by the parent so it survives the tree view
  // unmounting/remounting on every Segmented toggle.
  const treeCache = useRef<AssetListPage | null>(null);

  return (
    <Page>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Assets
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        The tables and files DataQ knows about. Health is rolled up across every suite that targets
        the asset.
      </Typography.Paragraph>
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
          <AssetsTreeView onOpen={onOpen} cacheRef={treeCache} />
        ) : (
          <AssetsTableView onOpen={onOpen} />
        )}
      </Flex>
    </Page>
  );
}

const EMPTY_DESCRIPTION = 'No assets yet — give a suite a run target and it will appear here.';

/**
 * Hard cap on how many rows the tree view will walk pages for (#925) — a tree render over an
 * unbounded workspace would eventually hang the browser tab.
 */
const TREE_HARD_BOUND = 2000;
/** One walked page — the server's own max (`_LIST_LIMIT_MAX` in
 *  `backend/app/api/v1/assets.py`), so the walk takes the fewest round trips. */
const TREE_PAGE_SIZE = 200;

/**
 * Walk `/assets` pages until every asset is fetched, `TREE_HARD_BOUND` is hit, or the server stops
 * returning rows — whichever comes first.
 */
async function fetchAllAssetsForTree(signal: AbortSignal): Promise<AssetListPage> {
  // Offset paging over a live population races concurrent writes (review finding): a delete below
  // the cursor shifts later rows down (one is skipped), an insert shifts them up (one repeats).
  const byId = new Map<string, AssetSummary>();
  let total: number | null = null;
  let offset = 0;
  while (byId.size < TREE_HARD_BOUND) {
    if (signal.aborted) break;
    const page = await listAssets({ limit: TREE_PAGE_SIZE, offset }, signal);
    total = total ?? page.total;
    for (const item of page.items) byId.set(item.id, item);
    if (page.items.length === 0) break;
    offset += page.items.length;
    if (byId.size >= total) break;
  }
  if (signal.aborted) {
    throw new DOMException('Asset tree walk aborted', 'AbortError');
  }
  return { items: [...byId.values()], total: total ?? byId.size };
}

function AssetsTreeView({
  onOpen,
  cacheRef,
}: {
  onOpen: (id: string) => void;
  /** Held by the parent (`Assets`) — see its comment. */
  cacheRef: RefObject<AssetListPage | null>;
}) {
  const { state, reload } = useAsyncData<AssetListPage>((signal) => {
    // A complete walk from an earlier mount of this view (a prior toggle to "By source" in this
    // same page visit) is still good.
    if (cacheRef.current) return Promise.resolve(cacheRef.current);
    return fetchAllAssetsForTree(signal).then((data) => {
      cacheRef.current = data;
      return data;
    });
  });
  return (
    <AsyncBody
      state={state}
      loadingText="Loading assets…"
      errorTitle="Failed to load assets"
      page
      onRetry={reload}
    >
      {({ items, total }) =>
        items.length === 0 ? (
          <Empty description={EMPTY_DESCRIPTION} />
        ) : (
          <Flex vertical gap={16} align="stretch">
            {/* Honest truncation (#925): never render a tree that silently
                dropped assets — say exactly how many are missing. */}
            {items.length < total && (
              <Alert
                type="warning"
                showIcon
                title={`Showing ${items.length} of ${total} assets`}
                description={
                  'The tree view is capped so the browser stays responsive — switch to ' +
                  '"All assets" to page through the rest.'
                }
              />
            )}
            <AssetsTree assets={items} onOpen={onOpen} />
          </Flex>
        )
      }
    </AsyncBody>
  );
}

const TABLE_PAGE_SIZE = 50;

function AssetsTableView({ onOpen }: { onOpen: (id: string) => void }) {
  const [page, setPage] = useState(1);
  // Forward the abort signal (#1107 review) so switching pages quickly — or toggling away from the
  // table entirely — cancels the superseded request at the network layer.
  const { state, reload } = useAsyncData((signal) =>
    listAssets({ limit: TABLE_PAGE_SIZE, offset: (page - 1) * TABLE_PAGE_SIZE }, signal),
  );
  // useAsyncData only re-fetches on `reload()` (its effect keys off a nonce, not the fetcher
  // identity — see its doc), so a page change must bump it explicitly.
  const onPageChange = (nextPage: number) => {
    setPage(nextPage);
    reload();
  };

  return (
    <AsyncBody
      state={state}
      loadingText="Loading assets…"
      errorTitle="Failed to load assets"
      page
      onRetry={reload}
    >
      {({ items, total }) =>
        total === 0 ? (
          <Empty description={EMPTY_DESCRIPTION} />
        ) : (
          <AssetsTable
            assets={items}
            onOpen={onOpen}
            pagination={{
              current: page,
              pageSize: TABLE_PAGE_SIZE,
              total,
              onChange: onPageChange,
              showSizeChanger: false,
            }}
          />
        )
      }
    </AsyncBody>
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

/** Connection-rooted drill-down over the assets (#802). */
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

function AssetsTable({
  assets,
  onOpen,
  pagination = false,
}: {
  assets: AssetSummary[];
  onOpen: (id: string) => void;
  /**
   * Server-side pagination config (#925) — `dataSource` is already the current page's rows, so
   * `Table` must NOT re-slice it client-side.
   */
  pagination?: TablePaginationConfig | false;
}) {
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
      pagination={pagination}
      onRow={(asset) => ({
        onClick: () => onOpen(asset.id),
        style: { cursor: 'pointer' },
      })}
    />
  );
}
